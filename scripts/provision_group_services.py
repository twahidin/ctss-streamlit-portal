"""Provision the per-group Railway services automatically via the Railway API.

Instead of clicking through the Railway dashboard once per group, this creates
a project and N services in one go — each linked to the mono-repo, with its
Root Directory, Watch Paths, and Start Command set, and a public domain
generated. Optionally it also emits the GROUP_CODES JSON using the *real*
domains it just created (so you never copy URLs by hand).

    python scripts/provision_group_services.py --repo you/ctss-streamlit-projects --groups 7
        → DRY RUN: prints exactly what it would create, touches nothing.

    python scripts/provision_group_services.py --repo you/ctss-streamlit-projects --groups 7 --yes
        → actually creates the project + 7 services (this uses your Railway plan).

    ... --yes --emit-codes --out group_codes.local.txt
        → also generates login codes + GROUP_CODES JSON from the live URLs.

Auth: uses the token from `railway login` (~/.railway/config.json) or the
RAILWAY_TOKEN / RAILWAY_API_TOKEN environment variable.

Prerequisites:
  * The mono-repo already exists and is seeded (scripts/seed_repo.py).
  * Railway's GitHub app has access to that repo (connect it once at
    railway.com → project → New Service → GitHub, or in GitHub settings).
    Deploying a *private* repo requires this connection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://backboard.railway.com/graphql/v2"
DEFAULT_START_COMMAND = "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0"


# ---------------------------------------------------------------------------
# Auth + GraphQL
# ---------------------------------------------------------------------------

def load_token() -> str:
    for env in ("RAILWAY_TOKEN", "RAILWAY_API_TOKEN"):
        if os.environ.get(env):
            return os.environ[env]
    cfg = Path.home() / ".railway" / "config.json"
    if cfg.exists():
        data = json.loads(cfg.read_text())
        token = data.get("user", {}).get("token") or data.get("token")
        if token:
            return token
    raise SystemExit(
        "No Railway token found. Run `railway login`, or set RAILWAY_TOKEN."
    )


class RailwayError(RuntimeError):
    pass


def gql(token: str, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ctss-portal-setup/1.0",  # Railway's Cloudflare blocks the default urllib UA
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RailwayError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}")
    except urllib.error.URLError as exc:
        raise RailwayError(f"Network error reaching Railway: {exc}")
    if payload.get("errors"):
        msgs = "; ".join(e.get("message", str(e)) for e in payload["errors"])
        raise RailwayError(msgs)
    return payload["data"]


# ---------------------------------------------------------------------------
# Friendly code generation (reuse make_group_codes if available)
# ---------------------------------------------------------------------------

def _code_generator():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from make_group_codes import _make_code  # type: ignore
        return _make_code
    except Exception:
        import secrets

        def _fallback(used: set[str]) -> str:
            while True:
                c = secrets.token_urlsafe(6)
                if c not in used:
                    used.add(c)
                    return c
        return _fallback


# ---------------------------------------------------------------------------
# Railway operations
# ---------------------------------------------------------------------------

def resolve_workspace(token: str, wanted: str | None) -> str:
    data = gql(token, "query{ me { workspaces { id name } } }")
    workspaces = data["me"]["workspaces"]
    if not workspaces:
        raise SystemExit("Your Railway account has no workspaces.")
    if wanted:
        for w in workspaces:
            if wanted in (w["id"], w["name"]):
                return w["id"]
        raise SystemExit(f"Workspace {wanted!r} not found. Have: {[w['name'] for w in workspaces]}")
    return workspaces[0]["id"]


def create_project(token: str, name: str, workspace_id: str) -> tuple[str, str]:
    """Create a project; return (project_id, default_environment_id)."""
    q = """
    mutation($input: ProjectCreateInput!) {
      projectCreate(input: $input) {
        id
        environments { edges { node { id name } } }
      }
    }"""
    data = gql(token, q, {"input": {
        "name": name,
        "workspaceId": workspace_id,
        "defaultEnvironmentName": "production",
    }})
    proj = data["projectCreate"]
    envs = [e["node"] for e in proj["environments"]["edges"]]
    env = next((e for e in envs if e["name"] == "production"), envs[0])
    return proj["id"], env["id"]


def get_project_env(token: str, project_id: str) -> str:
    q = "query($id: String!){ project(id:$id){ environments{ edges{ node{ id name } } } } }"
    envs = [e["node"] for e in gql(token, q, {"id": project_id})["project"]["environments"]["edges"]]
    return next((e["id"] for e in envs if e["name"] == "production"), envs[0]["id"])


def create_service(token: str, project_id: str, name: str, repo: str, branch: str) -> str:
    q = "mutation($input: ServiceCreateInput!){ serviceCreate(input:$input){ id } }"
    data = gql(token, q, {"input": {
        "projectId": project_id,
        "name": name,
        "branch": branch,
        "source": {"repo": repo},
    }})
    return data["serviceCreate"]["id"]


def configure_service(token: str, service_id: str, env_id: str, root_dir: str,
                      watch: list[str], start_command: str) -> None:
    q = """
    mutation($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
      serviceInstanceUpdate(serviceId:$serviceId, environmentId:$environmentId, input:$input)
    }"""
    gql(token, q, {
        "serviceId": service_id,
        "environmentId": env_id,
        "input": {
            "rootDirectory": root_dir,
            "watchPatterns": watch,
            "startCommand": start_command,
            "builder": "NIXPACKS",
        },
    })


def create_domain(token: str, service_id: str, env_id: str) -> str:
    q = "mutation($input: ServiceDomainCreateInput!){ serviceDomainCreate(input:$input){ domain } }"
    return gql(token, q, {"input": {"serviceId": service_id, "environmentId": env_id}})["serviceDomainCreate"]["domain"]


def deploy_service(token: str, service_id: str, env_id: str) -> None:
    q = "mutation($serviceId: String!, $environmentId: String!){ serviceInstanceDeployV2(serviceId:$serviceId, environmentId:$environmentId) }"
    try:
        gql(token, q, {"serviceId": service_id, "environmentId": env_id})
    except RailwayError:
        pass  # a deploy may already be in flight from serviceCreate; not fatal


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Provision per-group Railway services from the mono-repo.")
    p.add_argument("--repo", required=True, help="Mono-repo, e.g. you/ctss-streamlit-projects")
    p.add_argument("--groups", type=int, required=True, help="How many group services to create.")
    p.add_argument("--project-name", default="ctss-streamlit-projects", help="New project name.")
    p.add_argument("--project-id", help="Add services to this existing project instead of creating one.")
    p.add_argument("--workspace", help="Workspace id or name (default: first).")
    p.add_argument("--branch", default="main", help="Repo branch to deploy (default: main).")
    p.add_argument("--start-command", default=DEFAULT_START_COMMAND, help="Start command per service.")
    p.add_argument("--emit-codes", action="store_true", help="Also print GROUP_CODES JSON from the live URLs.")
    p.add_argument("--out", help="Write codes + JSON to this file (implies --emit-codes; keep secret).")
    p.add_argument("--yes", action="store_true", help="Actually create resources (default is a dry run).")
    args = p.parse_args()

    if args.groups < 1:
        raise SystemExit("--groups must be at least 1.")
    emit_codes = args.emit_codes or bool(args.out)

    plan = [(f"group-{i}", f"group-{i}", [f"group-{i}/**"]) for i in range(1, args.groups + 1)]

    print(f"\nRepo:          {args.repo} (branch {args.branch})")
    print(f"Project:       {args.project_id or '(new) ' + args.project_name}")
    print(f"Start command: {args.start_command}")
    print(f"Services to create ({args.groups}):")
    for name, root, watch in plan:
        print(f"  • {name:<10}  rootDir={root:<10}  watch={watch[0]}")

    if not args.yes:
        print("\nDRY RUN — nothing was created. Re-run with --yes to provision.\n")
        return 0

    token = load_token()

    if args.project_id:
        project_id = args.project_id
        env_id = get_project_env(token, project_id)
        print(f"\nUsing existing project {project_id}")
    else:
        workspace_id = resolve_workspace(token, args.workspace)
        project_id, env_id = create_project(token, args.project_name, workspace_id)
        print(f"\nCreated project {project_id}")

    results: list[tuple[str, str]] = []  # (group_id, domain)
    for name, root, watch in plan:
        print(f"  → {name}: creating service…", end="", flush=True)
        sid = create_service(token, project_id, name, args.repo, args.branch)
        configure_service(token, sid, env_id, root, watch, args.start_command)
        domain = create_domain(token, sid, env_id)
        deploy_service(token, sid, env_id)
        print(f" done → https://{domain}")
        results.append((name, domain))

    print("\nAll services created:\n")
    for gid, domain in results:
        print(f"  {gid:<10}  https://{domain}")

    if emit_codes:
        make_code = _code_generator()
        used: set[str] = set()
        mapping = {
            make_code(used): {"id": gid, "url": f"https://{domain}", "name": gid.replace("-", " ").title()}
            for gid, domain in results
        }
        json_line = json.dumps(mapping, separators=(",", ":"))
        print("\nGROUP_CODES (paste into the portal service):\n")
        print(json_line + "\n")
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write("CTSS Streamlit Portal — Group Codes (KEEP SECRET)\n\n")
                for code, info in mapping.items():
                    fh.write(f"{info['name']:<10}  {code:<16}  {info['url']}\n")
                fh.write("\nGROUP_CODES value:\n" + json_line + "\n")
            print(f"Saved to {args.out} (gitignored — do not share).")

    print("\nDone. Railway is now building each service; live URLs come up in ~1–3 min.")
    print("Next: deploy the portal itself and set GROUP_CODES to the value above.\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RailwayError as exc:
        print(f"\nRailway API error: {exc}\n", file=sys.stderr)
        raise SystemExit(1)
