# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI upload portal for a school Computing class. Student groups authenticate with a shared code, submit an `app.py` plus up to 4 feature `.py` modules (as file uploads or Google Drive/Colab links), and the portal validates, then commits them to a GitHub **mono-repo**. Each `group-N/` folder in that mono-repo is a separate Railway service that redeploys itself on push, producing a live Streamlit URL per group.

This portal repo does **not** contain the students' Streamlit apps — those live in the external mono-repo (`GITHUB_REPO`). This repo only produces commits into it.

## Commands

```bash
# Local dev server (hot reload)
uvicorn app.main:app --reload          # http://127.0.0.1:8000

# Run validator smoke tests (the only tests in the repo)
python -m app.validator                # prints "validator smoke tests passed" or asserts

# One-time: seed the external mono-repo with group-1..N placeholder apps
python scripts/seed_repo.py --groups 7 # needs GITHUB_TOKEN + GITHUB_REPO in env/.env

# Regenerate the student instruction slide deck (writes to ~/Desktop)
python scripts/build_student_deck.py
```

There is no test framework, linter, or build step configured. Validation logic is exercised by the inline `_run_smoke_tests()` in `app/validator.py` — extend it there when changing denylists or parsing.

## Architecture

Request flow for an upload (`app/main.py`):

1. `/verify` checks the group code against `config.GROUP_CODES` and returns the group's label + live URL.
2. `/upload` — per-group in-memory rate limit (`UPLOAD_COOLDOWN_SECONDS`), then for `app.py` and each feature slot: resolve content from **either** a file upload **or** a Drive/Colab link (`app/drive_fetch.py`), never both.
3. `requirements.txt` is generated **server-side** by `_assemble_requirements()` from the checkbox picker + free-text — students never upload one directly. `streamlit` is always included.
4. `validate_upload_bundle()` (`app/validator.py`) runs AST safety checks on every `.py` and a strict PyPI-only check on the generated requirements.
5. `GitHubCommitter.commit_group_files()` (`app/github_client.py`) writes all files as **one atomic tree-based commit** via the git-data API (blob → tree → commit → ref). This matters: one commit = one Railway webhook, not one per file. Uploads merge into the folder (untouched files are left alone).

Key design constraints:

- **Single-worker assumption.** Rate-limit and last-deploy state are plain module-level dicts (`_last_upload_at`, `_last_deploy_info`) in `main.py`. This is only correct because the portal runs as one Railway service with one worker. Don't scale to multiple workers without moving this state out.
- **`group_id` never comes from user input.** It's sourced from server-side `GROUP_CODES` config, and filenames are validated (no `/`, `\`, or leading dots) before the commit path `f"{group_id}/{filename}"` is built. This is what prevents path traversal in the committer.
- **The AST validator is defense-in-depth, not a sandbox.** It blocks obvious escapes (see `config.DENIED_*` sets) but each group's code still runs as its own real Railway service. Treat the denylists as a filter, not a security boundary.

## Configuration (`app/config.py`)

All runtime config is env-backed and loaded via `python-dotenv`. Required for the portal to actually commit (`is_configured()`): `GITHUB_TOKEN`, `GITHUB_REPO`, `GROUP_CODES`. `GROUP_CODES` is a **JSON string** mapping `code -> {id, url, name}`; see `.env.example`.

The security denylists and the curated library picker (checkboxes shown on the upload page) are **all defined as `Final` frozensets/tuples in `config.py`** — `DENIED_MODULES`, `DENIED_FROM_IMPORTS`, `DENIED_CALLS`, `DENIED_ATTRIBUTES`, `DENIED_ATTR_CALLS`, `BLOCKED_PACKAGES`, and `CURATED_LIBRARIES`. To allow a new student-usable library, add a `CuratedLibrary` entry (its `.name` becomes the exact PyPI requirement line). To tighten/loosen safety, edit the denylists and add a matching case to the validator smoke tests.

## Drive/Colab fetching (`app/drive_fetch.py`)

Anonymous GET against Drive's download endpoint (same mechanism as `gdown`; requires "Anyone with the link – Viewer"). If the fetched bytes are a Colab/Jupyter `.ipynb`, code is extracted in two passes: first a cell starting with `%%writefile <target_name>` (used verbatim), otherwise all code cells concatenated with notebook-only lines (`!pip`, `%magic`, `from google.colab …`) stripped. All failures raise `DriveFetchError` with a student-facing message.

## Deployment

`railway.json` runs `uvicorn app.main:app` with a `/healthz` healthcheck. See `README.md` for the full one-time setup (creating the mono-repo, the seven per-group Railway services with per-folder watch paths, and the portal service).

## Sensitive files

`group_codes.local.txt` and the hard-coded codes/URLs inside `scripts/build_student_deck.py` contain **real, live group access codes** — anyone with a code can overwrite that group's deployed app. Do not commit these to a public place, print them, or include them in artifacts/shared output. `.env` holds the `GITHUB_TOKEN`.
