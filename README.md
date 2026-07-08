# CTSS Streamlit Upload Portal

A FastAPI portal that lets student groups upload Python files and auto-deploys them as live Streamlit apps on Railway.

```
Upload Portal (FastAPI on Railway)
        ↓
GitHub mono-repo: ctss-streamlit-projects/
  ├── group-1/
  ├── group-2/
  └── ... group-7/
        ↓
7 Railway services, each watching one subfolder
        → group-1.up.railway.app, ..., group-7.up.railway.app
```

## Deploy on Railway (one-click template)

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/template/YOUR_TEMPLATE_CODE)

> Replace `YOUR_TEMPLATE_CODE` above with the code Railway gives you after you publish this repo as a template (see below). Until then, deploy straight from the repo via **New Project → Deploy from GitHub repo → `twahidin/ctss-streamlit-portal`**.

This template deploys **only the portal service**. The student Streamlit apps live in a *separate* GitHub mono-repo that the portal commits into (see the diagram above) — you create that mono-repo and its per-group Railway services separately (steps 4–5 under **One-time setup**).

**Variables the deployer must set** (Railway will prompt for these when configured as template variables):

| Variable       | Required | What it is                                                                 |
| -------------- | -------- | -------------------------------------------------------------------------- |
| `GITHUB_TOKEN` | yes      | GitHub PAT with `repo` scope — lets the portal commit to the mono-repo.    |
| `GITHUB_REPO`  | yes      | The mono-repo, e.g. `your-org/ctss-streamlit-projects`.                     |
| `GROUP_CODES`  | yes      | JSON map `code → {id, url, name}` (see `.env.example`). Keep it secret.     |

Start command and healthcheck are already defined in `railway.json` (`uvicorn app.main:app` + `/healthz`), so no build config is needed.

**To publish this repo as a reusable Railway template:**

1. Deploy this repo once (New Project → Deploy from GitHub repo).
2. On the service, add the three variables above under **Variables**.
3. Project **Settings → Publish as Template** → mark `GITHUB_TOKEN` / `GITHUB_REPO` / `GROUP_CODES` as user-supplied → Publish.
4. Copy the resulting `railway.com/template/…` code into the button URL above.

---

## How it works

1. A group enters their code, picks up to 3 `.py` files (must include `app.py`) and a `requirements.txt`.
2. The portal AST-validates the Python and PyPI-only-validates the requirements.
3. Files are committed atomically to that group's subfolder in the mono-repo.
4. Railway sees the push, redeploys the dedicated service, and the live URL refreshes within ~60s.

---

## One-time setup

### 1. GitHub

1. Create an empty private repo (or let the seed script create it). Name it something like `ctss-streamlit-projects`.
2. Create a personal access token with **`repo`** scope: <https://github.com/settings/tokens>.
3. Save the token; you'll set it as `GITHUB_TOKEN` in env.

### 2. Local prep

```bash
cd ctss-streamlit-portal
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: set GITHUB_TOKEN, GITHUB_REPO, GROUP_CODES.
```

### 3. Generate group codes (wizard)

Don't hand-write the JSON — run the wizard. It generates friendly codes like
`sky-tiger-42` and prints a table plus the ready-to-paste `GROUP_CODES` value:

```bash
python scripts/make_group_codes.py           # asks how many groups
python scripts/make_group_codes.py --groups 12 --out group_codes.local.txt
```

It builds the `GROUP_CODES` JSON for you (`id` = mono-repo folder, `url` = that
group's live Railway URL):

```json
{
  "sky-tiger-42": {"id": "group-1", "url": "https://group-1.up.railway.app", "name": "Group 1"},
  "red-otter-77": {"id": "group-2", "url": "https://group-2.up.railway.app", "name": "Group 2"}
}
```

Paste the single-line output as the portal service's `GROUP_CODES` variable (or into `.env` locally). Use `--url-template "https://grp{n}.up.railway.app"` if your live URLs follow a different pattern.

### 4. Seed the mono-repo

```bash
python scripts/seed_repo.py --groups 7
```

This creates `group-1/` through `group-7/`, each with a placeholder `app.py` and a minimal `requirements.txt`.

### 5. Create the Railway group services

**Option A — click-through admin app (easiest, no command line).** Deploy or run the Streamlit setup panel in [`admin/`](admin/README.md). It seeds the folders, creates every group service on Railway, and generates the `GROUP_CODES` list — all from buttons in a web page. Ideal for non-technical teachers.

```bash
pip install -r admin/requirements.txt && streamlit run admin/app.py
```

**Option B — one command.** Provision all group services with `scripts/provision_group_services.py`. It creates the project, one service per group (Root Directory, Watch Paths, and Start Command all set), generates a public domain for each, and can emit the `GROUP_CODES` JSON from the real URLs:

```bash
# Dry run first — prints the plan, creates nothing:
python scripts/provision_group_services.py --repo you/ctss-streamlit-projects --groups 7

# Provision for real, and generate the codes JSON from the live URLs:
python scripts/provision_group_services.py --repo you/ctss-streamlit-projects --groups 7 \
    --yes --emit-codes --out group_codes.local.txt
```

Auth uses your `railway login` session (or `RAILWAY_TOKEN`). Railway's GitHub app must have access to the mono-repo (connect it once when prompted, or in GitHub settings) — required for private repos.

**Option C — manual (Railway dashboard).** For **each** group folder, create one service connected to the mono-repo:

| Setting          | Value                                                              |
| ---------------- | ------------------------------------------------------------------ |
| Source           | `ctss-streamlit-projects` (the GitHub mono-repo)                   |
| Root Directory   | `group-N` (e.g. `group-1`)                                         |
| Watch Paths      | `group-N/**` — only that folder's commits trigger redeploys        |
| Start Command    | `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0` |
| Builder          | NIXPACKS (default)                                                 |

After deploy, set its public domain (Settings → Networking → Generate domain) and copy the URL into the matching `GROUP_CODES` entry.

### 6. Deploy the portal itself

Create one more Railway service from this repo (`ctss-streamlit-portal`):

- **Root Directory:** project root (default)
- **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (already in `railway.json`)
- **Env vars:** `GITHUB_TOKEN`, `GITHUB_REPO`, `GROUP_CODES`

Generate a public domain. Hand the URL out to students.

---

## Local development

```bash
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

Run validator smoke tests:

```bash
python -m app.validator
```

---

## Adding or rotating group codes

Codes live in the `GROUP_CODES` env var on Railway. To rotate:

1. Generate a new code (`python -c "import secrets; print(secrets.token_urlsafe(8))"`).
2. Edit `GROUP_CODES` on the portal Railway service.
3. Redeploy (Railway auto-redeploys on env-var change).
4. Hand the new code out.

To revoke a code, just remove its entry.

---

## Safety model

The portal **rejects** Python that contains:

- Imports of `subprocess`, `socket`, `paramiko`, `pty`, `ctypes`, `pickle`, `marshal`, `shutil`, etc.
- Calls to `eval`, `exec`, `compile`, `__import__`.
- Calls like `os.system`, `os.popen`, `os.exec*`, `pickle.loads`.
- Attribute access like `.__globals__`, `.__subclasses__`, `.__class__`.

`requirements.txt` rejects:

- VCS / file / URL specs (`git+...`, `-e .`)
- Blocked packages (`paramiko`, `pwntools`, etc.)
- More than 30 packages

This is **defense in depth, not a sandbox.** Each group's code still runs as its own Railway service. A student determined to escape can; the goal is to filter out the obvious things, not to be airtight.

---

## File map

```
app/
  main.py            FastAPI routes + rate limit + commit orchestration
  config.py          env loader, group codes, denylists, limits
  validator.py       AST safety checks; run with `python -m app.validator`
  github_client.py   atomic commit via PyGithub git-data API
  models.py          pydantic schemas
templates/
  index.html         two-state upload UI (code -> upload)
  success.html       deployed confirmation
  error.html         friendly validation errors
static/style.css     small custom layer over Tailwind
group_template/      placeholder Streamlit app + requirements
scripts/seed_repo.py one-time mono-repo seeding
railway.json         portal deploy config
```
