"""FastAPI entrypoint for the CTSS Streamlit upload portal."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import re

from . import config
from .drive_fetch import DriveFetchError, fetch_code_from_url
from .github_client import GitHubCommitter
from .validator import validate_upload_bundle


_FEATURE_FILENAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*\.py$")

logger = logging.getLogger("ctss.portal")
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="CTSS Streamlit Upload Portal")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# In-memory state: rate limit timestamps + last-deploy info per group.
# A dict is fine — the portal is a single Railway service with one worker.
# ---------------------------------------------------------------------------

_last_upload_at: dict[str, float] = {}
_last_deploy_info: dict[str, dict[str, str]] = {}


def _committer() -> GitHubCommitter:
    if not config.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Portal is not fully configured. Contact your teacher.",
        )
    return GitHubCommitter(token=config.GITHUB_TOKEN, repo_name=config.GITHUB_REPO)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {"libraries_by_category": config.curated_libraries_grouped()},
    )


@app.post("/verify")
async def verify(code: str = Form(...)) -> JSONResponse:
    """Confirm a group code and return the group label + URL."""
    info = config.GROUP_CODES.get(code.strip())
    if not info:
        return JSONResponse(
            {"ok": False, "error": "That code doesn't match any group."},
            status_code=200,
        )
    return JSONResponse(
        {
            "ok": True,
            "group_id": info.id,
            "group_name": info.name,
            "url": info.url,
        }
    )


def _assemble_requirements(selected: list[str], custom: str) -> str:
    """Build a requirements.txt body from the picker + free-text textarea.

    `streamlit` is always included. Curated entries that aren't actually known
    are silently dropped (defensive against form tampering); custom entries are
    kept verbatim so the existing validator can give precise error messages.
    """
    known = config.known_library_names()
    seen: set[str] = set()
    lines: list[str] = []

    def _add(line: str) -> None:
        # Use lowercased package-name prefix as the dedupe key.
        key = line.lower().split("==")[0].split(">")[0].split("<")[0].strip()
        if key and key not in seen:
            seen.add(key)
            lines.append(line)

    _add("streamlit")
    for name in selected:
        if name in known:
            _add(name)

    for raw in (custom or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            _add(line)

    return "\n".join(lines) + "\n"


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    code: str = Form(...),
    app_py: UploadFile | None = File(None),
    drive_url: str = Form(""),
    feature1_name: str = Form(""),
    feature1_file: UploadFile | None = File(None),
    feature1_url: str = Form(""),
    feature2_name: str = Form(""),
    feature2_file: UploadFile | None = File(None),
    feature2_url: str = Form(""),
    feature3_name: str = Form(""),
    feature3_file: UploadFile | None = File(None),
    feature3_url: str = Form(""),
    feature4_name: str = Form(""),
    feature4_file: UploadFile | None = File(None),
    feature4_url: str = Form(""),
    libraries: list[str] = Form([]),
    custom_libraries: str = Form(""),
) -> HTMLResponse:
    info = config.GROUP_CODES.get(code.strip())
    if not info:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"errors": ["Invalid group code."]},
            status_code=400,
        )

    # Rate limit per group.
    now = time.monotonic()
    last = _last_upload_at.get(info.id, 0.0)
    if now - last < config.UPLOAD_COOLDOWN_SECONDS:
        wait = int(config.UPLOAD_COOLDOWN_SECONDS - (now - last))
        return templates.TemplateResponse(
            request,
            "error.html",
            {"errors": [f"Please wait {wait}s before uploading again."]},
            status_code=429,
        )

    files: dict[str, str] = {}
    errors: list[str] = []

    def _bail(msgs: list[str], status: int = 400) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "error.html", {"errors": msgs}, status_code=status,
        )

    # ----- app.py: file upload OR Drive/Colab link -----
    drive_url = (drive_url or "").strip()
    if drive_url:
        try:
            fetched = fetch_code_from_url(drive_url, target_name=config.REQUIRED_FILE)
        except DriveFetchError as exc:
            return _bail([f"app.py: {exc}"])
        files[config.REQUIRED_FILE] = fetched.code
    else:
        if not app_py or not app_py.filename:
            return _bail(["Choose an app.py file to upload, or paste a Drive/Colab share link."])
        raw = await app_py.read()
        try:
            files[config.REQUIRED_FILE] = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _bail(["app.py must be UTF-8 text."])

    # ----- Up to 4 feature files: each has a name + (file OR url) -----
    feature_slots: list[tuple[str, UploadFile | None, str]] = [
        (feature1_name, feature1_file, feature1_url),
        (feature2_name, feature2_file, feature2_url),
        (feature3_name, feature3_file, feature3_url),
        (feature4_name, feature4_file, feature4_url),
    ]

    seen_names = {config.REQUIRED_FILE}
    for idx, (raw_name, file_upload, url) in enumerate(feature_slots, start=1):
        name = (raw_name or "").strip()
        url = (url or "").strip()
        has_file = bool(file_upload and file_upload.filename)
        has_url = bool(url)

        # Skip empty slots silently.
        if not name and not has_file and not has_url:
            continue

        slot_label = f"Feature file {idx}"

        if not name:
            errors.append(f"{slot_label}: please give the file a name (e.g. weather_loader.py).")
            continue

        if not _FEATURE_FILENAME_RE.match(name):
            errors.append(
                f"{slot_label}: '{name}' isn't a valid filename. "
                "Use letters, numbers and underscores only, and end with .py "
                "(e.g. weather_loader.py)."
            )
            continue

        if name in seen_names:
            errors.append(f"{slot_label}: duplicate filename '{name}'.")
            continue
        seen_names.add(name)

        if has_url and has_file:
            errors.append(
                f"{slot_label} ({name}): pick EITHER a file OR a link, not both."
            )
            continue

        if not has_url and not has_file:
            errors.append(
                f"{slot_label} ({name}): upload a file or paste a Drive/Colab link."
            )
            continue

        # Resolve content.
        try:
            if has_url:
                fetched = fetch_code_from_url(url, target_name=name)
                files[name] = fetched.code
            else:
                # has_file
                assert file_upload is not None  # for type-checker
                raw = await file_upload.read()
                try:
                    files[name] = raw.decode("utf-8")
                except UnicodeDecodeError:
                    errors.append(f"{slot_label} ({name}): file must be UTF-8 text.")
                    continue
        except DriveFetchError as exc:
            errors.append(f"{slot_label} ({name}): {exc}")
            continue

    if errors:
        return _bail(errors)

    # Generate requirements.txt server-side from the picker + custom textarea.
    files[config.REQUIREMENTS_FILE] = _assemble_requirements(libraries, custom_libraries)

    # AST + requirements validation.
    ok, errors = validate_upload_bundle(files)
    if not ok:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"errors": errors},
            status_code=400,
        )

    # Commit to GitHub.
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    message = f"{info.id}: upload from portal at {timestamp}"
    try:
        sha = _committer().commit_group_files(info.id, files, message)
    except Exception as exc:
        logger.exception("GitHub commit failed for %s", info.id)
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "errors": [
                    "We couldn't push your files to GitHub. Try again in a minute, "
                    "and tell your teacher if it keeps failing.",
                    f"Details: {exc.__class__.__name__}",
                ],
            },
            status_code=502,
        )

    _last_upload_at[info.id] = now
    _last_deploy_info[info.id] = {
        "commit_sha": sha,
        "deployed_at": timestamp,
        "url": info.url,
    }

    return templates.TemplateResponse(
        request,
        "success.html",
        {
            "url": info.url,
            "group_name": info.name,
            "commit_sha": sha[:7],
        },
    )


@app.get("/status/{group_id}")
async def status(group_id: str) -> JSONResponse:
    info = next((g for g in config.GROUP_CODES.values() if g.id == group_id), None)
    if not info:
        raise HTTPException(status_code=404, detail="Unknown group.")
    last = _last_deploy_info.get(group_id)
    return JSONResponse(
        {
            "group_id": group_id,
            "group_name": info.name,
            "url": info.url,
            "last_deploy": last,
        }
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
