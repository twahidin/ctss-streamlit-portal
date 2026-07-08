# Group Setup — Streamlit admin app

A click-through setup panel for teachers. It does the three setup jobs without a
command line:

1. **Create the storage folders** — a `group-1 … group-N` folder per team in your
   GitHub repo, each with a starter app.
2. **Create the group websites** — one live Railway service per team (root
   directory, watch paths, start command, and public link all set).
3. **Generate the team codes** — each team's secret code plus the `GROUP_CODES`
   list to paste into the Portal (built from the real live URLs).

## Run it locally

```bash
pip install -r admin/requirements.txt
streamlit run admin/app.py
```

Then open the page it prints, fill in your details, and click the buttons.

## Deploy it on Railway (no terminal for teachers)

Create a Railway service from this repo with **Root Directory = `admin`**. The
start command is already in `admin/railway.json`. Give it a public domain and
teachers can do the whole setup from a web page.

## What you'll need

- **GitHub token** — with `repo` access (same one the Portal uses).
- **GitHub storage repo** — e.g. `your-username/ctss-streamlit-projects`.
- **Railway token** — from railway.com → Account → Tokens (only needed for step 2).

Tokens are entered as password fields and are never stored by the app.
