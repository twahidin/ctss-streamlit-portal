"""CTSS Portal — Group Setup (Streamlit admin app).

A friendly, click-through setup panel for teachers. It does the three setup jobs
that used to need the command line:

  1. Populate  — create a group-1 … group-N folder for each team in your GitHub
                 storage repo, each with a starter Streamlit app.
  2. Create    — make one live website per team on Railway (root directory,
                 watch paths, start command, and a public link all set).
  3. Codes     — generate each team's secret code and the GROUP_CODES list to
                 paste into the Portal.

Run locally:  streamlit run admin/app.py
Or deploy it on Railway (see admin/README.md) so there's no terminal at all.

This app is self-contained — it talks to the GitHub and Railway APIs directly.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request

import streamlit as st

try:
    from github import Github
    from github.GithubException import UnknownObjectException  # type: ignore[assignment]
except Exception:  # pragma: no cover - surfaced in the UI
    Github = None  # type: ignore

    class UnknownObjectException(Exception):  # fallback so the name is always bound
        pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RAILWAY_API = "https://backboard.railway.com/graphql/v2"
START_COMMAND = "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0"

PLACEHOLDER_APP = (
    "import streamlit as st\n\n"
    "st.title(\"Your app will appear here\")\n"
    "st.write(\"Send in your files through the Portal and this page will update.\")\n"
)
PLACEHOLDER_REQS = "streamlit\n"

ADJECTIVES = [
    "sky", "red", "blue", "jade", "gold", "dawn", "wave", "moss", "lake", "mint",
    "pine", "oak", "dusk", "rose", "snow", "rust", "teal", "sage", "opal", "coral",
    "amber", "ivory", "ruby", "onyx", "cloud", "storm", "river", "ember", "frost", "lunar",
]
ANIMALS = [
    "tiger", "otter", "heron", "panda", "fox", "lynx", "crane", "whale", "koala", "falcon",
    "marten", "bison", "eagle", "puma", "ibis", "gecko", "raven", "seal", "moth", "wren",
    "hawk", "bear", "wolf", "lark", "swan", "boar", "mole", "newt", "shrew", "vole",
]


def make_code(used: set[str]) -> str:
    while True:
        code = f"{secrets.choice(ADJECTIVES)}-{secrets.choice(ANIMALS)}-{secrets.randbelow(90) + 10}"
        if code not in used:
            used.add(code)
            return code


# ---------------------------------------------------------------------------
# GitHub: populate the storage repo
# ---------------------------------------------------------------------------

def seed_repo(token: str, repo_name: str, n_groups: int, log) -> None:
    if Github is None:
        raise RuntimeError("PyGithub is not installed. Add 'PyGithub' to requirements.")
    repo = Github(token).get_repo(repo_name)
    for i in range(1, n_groups + 1):
        for path, content in (
            (f"group-{i}/app.py", PLACEHOLDER_APP),
            (f"group-{i}/requirements.txt", PLACEHOLDER_REQS),
        ):
            try:
                existing = repo.get_contents(path)
                if isinstance(existing, list):  # only happens for a directory path
                    existing = existing[0]
                repo.update_file(path, f"Seed {path}", content, existing.sha)
            except UnknownObjectException:
                repo.create_file(path, f"Seed {path}", content)
        log(f"group-{i}")


# ---------------------------------------------------------------------------
# Railway: create one service per group
# ---------------------------------------------------------------------------

class RailwayError(RuntimeError):
    pass


def rw(token: str, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        RAILWAY_API, data=body,
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
        raise RailwayError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as exc:
        raise RailwayError(f"Could not reach Railway: {exc}")
    if payload.get("errors"):
        raise RailwayError("; ".join(e.get("message", str(e)) for e in payload["errors"]))
    return payload["data"]


def provision_services(token: str, repo: str, n_groups: int, project_name: str,
                       branch: str, log) -> dict[str, str]:
    """Create a project + N services from the repo. Returns {group_id: live_url}."""
    workspaces = rw(token, "query{ me { workspaces { id name } } }")["me"]["workspaces"]
    if not workspaces:
        raise RailwayError("Your Railway account has no workspace.")
    ws_id = workspaces[0]["id"]

    proj = rw(token,
        "mutation($input: ProjectCreateInput!){ projectCreate(input:$input){ id environments{ edges{ node{ id name } } } } }",
        {"input": {"name": project_name, "workspaceId": ws_id, "defaultEnvironmentName": "production"}},
    )["projectCreate"]
    envs = [e["node"] for e in proj["environments"]["edges"]]
    env_id = next((e["id"] for e in envs if e["name"] == "production"), envs[0]["id"])
    project_id = proj["id"]
    log(f"Created project '{project_name}'")

    urls: dict[str, str] = {}
    for i in range(1, n_groups + 1):
        name = f"group-{i}"
        sid = rw(token,
            "mutation($input: ServiceCreateInput!){ serviceCreate(input:$input){ id } }",
            {"input": {"projectId": project_id, "name": name, "branch": branch, "source": {"repo": repo}}},
        )["serviceCreate"]["id"]
        rw(token,
            "mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){ serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i) }",
            {"s": sid, "e": env_id, "i": {
                "rootDirectory": name, "watchPatterns": [f"{name}/**"],
                "startCommand": START_COMMAND, "builder": "NIXPACKS"}},
        )
        domain = rw(token,
            "mutation($input: ServiceDomainCreateInput!){ serviceDomainCreate(input:$input){ domain } }",
            {"input": {"serviceId": sid, "environmentId": env_id}},
        )["serviceDomainCreate"]["domain"]
        try:
            rw(token,
               "mutation($s:String!,$e:String!){ serviceInstanceDeployV2(serviceId:$s,environmentId:$e) }",
               {"s": sid, "e": env_id})
        except RailwayError:
            pass
        urls[name] = f"https://{domain}"
        log(f"{name} → {urls[name]}")
    return urls


def build_group_codes(urls: dict[str, str]) -> dict:
    used: set[str] = set()
    return {
        make_code(used): {"id": gid, "url": url, "name": gid.replace("-", " ").title()}
        for gid, url in urls.items()
    }


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="CTSS Portal — Group Setup", page_icon="🎛️", layout="centered")
st.title("🎛️ Group Setup")
st.caption("Set up your class's group apps by clicking buttons — no command line needed.")

ss = st.session_state
ss.setdefault("urls", {})
ss.setdefault("codes", {})

with st.expander("① Your details", expanded=True):
    gh_token = st.text_input("GitHub token", type="password",
                             help="The token from the setup guide (lets us save files).")
    gh_repo = st.text_input("GitHub storage repo", placeholder="your-username/ctss-streamlit-projects")
    n_groups = st.number_input("How many groups?", min_value=1, max_value=50, value=7, step=1)
    rw_token = st.text_input("Railway token", type="password",
                             help="From railway.com → Account → Tokens. Needed to create the group websites.")
    project_name = st.text_input("Railway project name", value="ctss-streamlit-projects")
    branch = st.text_input("Repo branch", value="main")

st.divider()

# --- Step 1: populate ---
st.subheader("1 · Create the storage folders")
st.write("Adds a `group-1 … group-N` folder for each team, each with a starter app.")
if st.button("Create folders on GitHub", type="primary", use_container_width=True):
    if not (gh_token and gh_repo):
        st.error("Please enter your GitHub token and storage repo above.")
    else:
        prog = st.empty()
        done: list[str] = []
        try:
            seed_repo(gh_token, gh_repo, int(n_groups), lambda m: (done.append(m), prog.info(f"Created {', '.join(done)}")))
            st.success(f"Done — created {len(done)} group folders in {gh_repo}.")
        except Exception as exc:
            st.error(f"Couldn't create folders: {exc}")

st.divider()

# --- Step 2: provision ---
st.subheader("2 · Create the group websites")
st.write("Makes one live website per team on Railway, each with its own link.")
st.info("Railway's GitHub app must have access to the repo. Do the folders (step 1) first.", icon="ℹ️")
if st.button("Create websites on Railway", type="primary", use_container_width=True):
    if not (rw_token and gh_repo):
        st.error("Please enter your Railway token and GitHub repo above.")
    else:
        prog = st.empty()
        lines: list[str] = []
        try:
            urls = provision_services(rw_token, gh_repo, int(n_groups), project_name, branch,
                                      lambda m: (lines.append(m), prog.info("\n".join(lines))))
            ss.urls = urls
            st.success(f"Created {len(urls)} group websites.")
        except Exception as exc:
            st.error(f"Couldn't create websites: {exc}")

if ss.urls:
    st.dataframe(
        [{"Group": g, "Live URL": u} for g, u in ss.urls.items()],
        use_container_width=True, hide_index=True,
    )

st.divider()

# --- Step 3: codes ---
st.subheader("3 · Generate the team codes")
st.write("Creates each team's secret code and the **GROUP_CODES** list to paste into the Portal.")
col1, col2 = st.columns(2)
gen_real = col1.button("Use the real website links", use_container_width=True,
                       disabled=not ss.urls, help="Available after step 2.")
gen_tmpl = col2.button("Use placeholder links", use_container_width=True,
                       help="If you haven't created the websites yet.")

if gen_real and ss.urls:
    ss.codes = build_group_codes(ss.urls)
elif gen_tmpl:
    tmpl = {f"group-{i}": f"https://group-{i}.up.railway.app" for i in range(1, int(n_groups) + 1)}
    ss.codes = build_group_codes(tmpl)

if ss.codes:
    st.dataframe(
        [{"Team": info["name"], "Secret code": code, "Live URL": info["url"]}
         for code, info in ss.codes.items()],
        use_container_width=True, hide_index=True,
    )
    json_line = json.dumps(ss.codes, separators=(",", ":"))
    st.write("**GROUP_CODES** — paste this into your Portal's settings:")
    st.code(json_line, language="json")
    st.download_button("Download codes (keep secret)", data=json_line,
                       file_name="group_codes.json", mime="application/json",
                       use_container_width=True)
    st.caption("Give each team only their own code. Keep this file private.")
