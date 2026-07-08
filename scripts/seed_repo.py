"""One-time seeding script.

Creates the mono-repo (if missing) and seeds N group folders, each with a
placeholder Streamlit app and a minimal requirements.txt.

Usage:
    GITHUB_TOKEN=... GITHUB_REPO=ctss/ctss-streamlit-projects \\
    python scripts/seed_repo.py --groups 7
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from github import Github, GithubException, UnknownObjectException

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "group_template"

PLACEHOLDER_APP = (TEMPLATE_DIR / "app.py").read_text(encoding="utf-8")
PLACEHOLDER_REQS = (TEMPLATE_DIR / "requirements.txt").read_text(encoding="utf-8")

TOP_README = """# CTSS Streamlit Projects

This repo holds Streamlit apps for the Computing class at Clementi Town Secondary School.

Each `group-N/` folder is one Railway service. Files are pushed here by the upload portal.
Do not edit files directly — edit and re-upload via the portal.
"""

TOP_GITIGNORE = """__pycache__/
*.pyc
.venv/
.env
.DS_Store
"""


def _ensure_repo(client: Github, repo_full_name: str):
    try:
        return client.get_repo(repo_full_name)
    except UnknownObjectException:
        org_or_user, name = repo_full_name.split("/", 1)
        try:
            org = client.get_organization(org_or_user)
            return org.create_repo(name, private=True, auto_init=True)
        except GithubException:
            user = client.get_user()
            return user.create_repo(name, private=True, auto_init=True)


def _put_file(repo, path: str, content: str, message: str) -> None:
    try:
        existing = repo.get_contents(path)
        repo.update_file(path, message, content, existing.sha)
        print(f"  updated {path}")
    except UnknownObjectException:
        repo.create_file(path, message, content)
        print(f"  created {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", type=int, default=7)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    repo_name = os.environ.get("GITHUB_REPO")
    if not token or not repo_name:
        print("Set GITHUB_TOKEN and GITHUB_REPO before running.", file=sys.stderr)
        return 1

    client = Github(token)
    repo = _ensure_repo(client, repo_name)
    print(f"Using repo: {repo.full_name}")

    _put_file(repo, "README.md", TOP_README, "Add top-level README")
    _put_file(repo, ".gitignore", TOP_GITIGNORE, "Add .gitignore")

    for n in range(1, args.groups + 1):
        gid = f"group-{n}"
        print(f"Seeding {gid}/")
        _put_file(repo, f"{gid}/app.py", PLACEHOLDER_APP, f"Seed {gid}/app.py")
        _put_file(repo, f"{gid}/requirements.txt", PLACEHOLDER_REQS, f"Seed {gid}/requirements.txt")

    print("Done. Now create the Railway services for each folder (see README).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
