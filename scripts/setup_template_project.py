"""Build the two-service source project for the Railway template.

Creates one Railway project containing BOTH services, from the same repo:

  * portal — the FastAPI upload portal (repo root; uses ./railway.json)
  * admin  — the Streamlit setup app (Root Directory = admin; uses admin/railway.json)

Once this project exists you can publish it as a multi-service template, so that
whoever deploys the template gets the portal AND the admin app in one click.

    python scripts/setup_template_project.py                 # DRY RUN — plan only
    python scripts/setup_template_project.py --yes           # create the project + 2 services
    python scripts/setup_template_project.py --yes --publish # also publish it as a template (API)

Auth: uses your `railway login` session (~/.railway/config.json) or RAILWAY_TOKEN.

Notes:
  * The services deploy from the public portal repo, so Railway needs GitHub access
    to it (public repos usually just work; connect the GitHub app if prompted).
  * Publishing via --publish is best-effort. The dashboard's "Publish as Template"
    gives a nicer UI for marking GITHUB_TOKEN / GITHUB_REPO / GROUP_CODES as
    user-supplied inputs; prefer it if the API publish needs tweaking.
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
DEFAULT_REPO = "twahidin/ctss-streamlit-portal"


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
    raise SystemExit("No Railway token found. Run `railway login`, or set RAILWAY_TOKEN.")


class RailwayError(RuntimeError):
    pass


def gql(token: str, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
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
        raise RailwayError("; ".join(e.get("message", str(e)) for e in payload["errors"]))
    return payload["data"]


def create_project(token: str, name: str, workspace_id: str | None) -> tuple[str, str]:
    q = """mutation($input: ProjectCreateInput!){
      projectCreate(input:$input){ id environments{ edges{ node{ id name } } } }
    }"""
    proj_input: dict = {"name": name, "defaultEnvironmentName": "production"}
    if workspace_id:  # API tokens omit this; Railway infers the workspace
        proj_input["workspaceId"] = workspace_id
    proj = gql(token, q, {"input": proj_input})["projectCreate"]
    envs = [e["node"] for e in proj["environments"]["edges"]]
    env = next((e for e in envs if e["name"] == "production"), envs[0])
    return proj["id"], env["id"]


def create_service(token: str, project_id: str, name: str, repo: str, branch: str,
                   root_dir: str | None) -> str:
    sid = gql(token,
        "mutation($input: ServiceCreateInput!){ serviceCreate(input:$input){ id } }",
        {"input": {"projectId": project_id, "name": name, "branch": branch, "source": {"repo": repo}}},
    )["serviceCreate"]["id"]
    if root_dir:
        gql(token,
            "mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){ serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i) }",
            {"s": sid, "e": _ENV[0], "i": {"rootDirectory": root_dir}})
    return sid


def create_domain(token: str, service_id: str, env_id: str) -> str:
    return gql(token,
        "mutation($input: ServiceDomainCreateInput!){ serviceDomainCreate(input:$input){ domain } }",
        {"input": {"serviceId": service_id, "environmentId": env_id}},
    )["serviceDomainCreate"]["domain"]


_ENV: list[str] = [""]  # holds the environment id for create_service's root-dir update


def main() -> int:
    p = argparse.ArgumentParser(description="Create the two-service template source project.")
    p.add_argument("--repo", default=DEFAULT_REPO, help=f"Portal repo (default: {DEFAULT_REPO}).")
    p.add_argument("--branch", default="main")
    p.add_argument("--project-name", default="ctss-streamlit-portal-template")
    p.add_argument("--workspace", help="Workspace id or name (default: first).")
    p.add_argument("--yes", action="store_true", help="Actually create resources (default: dry run).")
    p.add_argument("--publish", action="store_true", help="Also try to publish it as a template (API).")
    args = p.parse_args()

    print(f"\nProject:  (new) {args.project_name}")
    print(f"Repo:     {args.repo} (branch {args.branch})")
    print("Services:")
    print("  • portal   root=/       → ./railway.json (uvicorn app.main:app)")
    print("  • admin    root=admin   → admin/railway.json (streamlit run app.py)")

    if not args.yes:
        print("\nDRY RUN — nothing created. Re-run with --yes to build the project.\n")
        return 0

    token = load_token()
    # User-session tokens (railway login) can list workspaces and must pass a workspaceId.
    # Dashboard API tokens can't use `me`; projectCreate infers their workspace, so ws=None.
    try:
        workspaces = gql(token, "query{ me { workspaces { id name } } }")["me"]["workspaces"]
    except RailwayError:
        workspaces = []
    if workspaces:
        ws = next((w["id"] for w in workspaces if args.workspace in (w["id"], w["name"])), None) \
            if args.workspace else workspaces[0]["id"]
        if ws is None:
            raise SystemExit(f"Workspace {args.workspace!r} not found.")
    else:
        ws = None

    project_id, env_id = create_project(token, args.project_name, ws)
    _ENV[0] = env_id
    print(f"\nCreated project {project_id}")

    portal_id = create_service(token, project_id, "portal", args.repo, args.branch, None)
    portal_domain = create_domain(token, portal_id, env_id)
    print(f"  portal → https://{portal_domain}")

    admin_id = create_service(token, project_id, "admin", args.repo, args.branch, "admin")
    admin_domain = create_domain(token, admin_id, env_id)
    print(f"  admin  → https://{admin_domain}")

    print("\nTwo-service project is ready.")
    print(f"Open it: https://railway.com/project/{project_id}")

    if args.publish:
        print("\nAttempting to publish as a template…")
        try:
            gen = gql(token,
                "mutation($input: TemplateGenerateInput!){ templateGenerate(input:$input){ id } }",
                {"input": {"projectId": project_id, "environmentId": env_id}})
            template_id = gen["templateGenerate"]["id"]
            gql(token,
                "mutation($id:String!,$input:TemplatePublishInput!){ templatePublish(id:$id, input:$input) }",
                {"id": template_id, "input": {
                    "workspaceId": ws,
                    "category": "Web",
                    "description": "Streamlit upload portal + click-through group setup admin app.",
                }})
            print(f"Published. Template id: {template_id}")
        except RailwayError as exc:
            print(f"API publish didn't complete ({exc}).")
            print("Use the dashboard instead: project → Settings → Publish as Template,")
            print("and mark GITHUB_TOKEN / GITHUB_REPO / GROUP_CODES as user-supplied.")
    else:
        print("\nNext: in the dashboard open the project → Settings → Publish as Template,")
        print("mark GITHUB_TOKEN / GITHUB_REPO / GROUP_CODES as user-supplied, and publish.")
    print()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RailwayError as exc:
        print(f"\nRailway API error: {exc}\n", file=sys.stderr)
        raise SystemExit(1)
