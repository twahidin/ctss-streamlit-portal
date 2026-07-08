"""Fetch a student's app.py from a Google Drive / Colab share link.

The link must be set to 'Anyone with the link – Viewer'. We do an anonymous
GET against drive.google.com's download endpoint (no OAuth) — this is the same
mechanism `gdown` uses for public files.

We accept three URL shapes:
    https://drive.google.com/file/d/<ID>/view...
    https://drive.google.com/open?id=<ID>
    https://colab.research.google.com/drive/<ID>...

If the fetched content is an .ipynb (Colab notebook), we extract Python source
in two passes:
    1. If a code cell begins with '%%writefile app.py', use that cell's body.
    2. Otherwise, concatenate all code cells and strip notebook noise lines
       (`!pip ...`, `%matplotlib`, `from google.colab ...`, etc.).

Raises DriveFetchError with a user-friendly message on every failure.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

MAX_FETCH_BYTES = 2 * 1024 * 1024  # 2 MB — generous cap for notebooks
USER_AGENT = "ctss-portal/1.0"
TIMEOUT_SECONDS = 30


class DriveFetchError(Exception):
    """User-friendly error suitable for showing in the portal UI."""


_FILE_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)"),
    re.compile(r"drive\.google\.com/open\?[^#]*id=([A-Za-z0-9_-]+)"),
    re.compile(r"drive\.google\.com/uc\?[^#]*id=([A-Za-z0-9_-]+)"),
    re.compile(r"colab\.research\.google\.com/drive/([A-Za-z0-9_-]+)"),
)


@dataclass(frozen=True)
class FetchedSource:
    code: str
    source_label: str  # short string we surface in logs / success messages


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def extract_file_id(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise DriveFetchError("Please paste a Google Drive or Colab share link.")
    for pat in _FILE_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    raise DriveFetchError(
        "That doesn't look like a Google Drive or Colab link. "
        "Expected something like https://drive.google.com/file/d/... "
        "or https://colab.research.google.com/drive/..."
    )


# ---------------------------------------------------------------------------
# Anonymous fetch
# ---------------------------------------------------------------------------

def _fetch_drive_bytes(file_id: str) -> bytes:
    download_url = f"https://drive.google.com/uc?id={file_id}&export=download"
    req = urllib.request.Request(download_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read(MAX_FETCH_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            raise DriveFetchError(
                "We couldn't open that file. In Drive/Colab, set sharing to "
                "'Anyone with the link – Viewer' and try again."
            ) from exc
        raise DriveFetchError(
            f"Drive returned HTTP {exc.code}. Try again in a minute."
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DriveFetchError(
            "We couldn't reach Google Drive just now. Check your link and try again."
        ) from exc

    if len(data) > MAX_FETCH_BYTES:
        raise DriveFetchError(
            f"That file is bigger than {MAX_FETCH_BYTES // (1024 * 1024)} MB. "
            "Trim it down or download + upload manually."
        )

    # Drive serves an HTML "virus scan" interstitial for files >100MB or when
    # access is wrong. Detect and surface clearly.
    if "text/html" in content_type.lower():
        raise DriveFetchError(
            "Drive returned a sign-in page instead of the file. "
            "Make sure sharing is set to 'Anyone with the link – Viewer'."
        )
    return data


# ---------------------------------------------------------------------------
# Notebook extraction
# ---------------------------------------------------------------------------

_DROP_LINE_PREFIXES = ("!", "%", "from google.colab", "import google.colab")


def _flatten_source(source) -> str:
    if isinstance(source, list):
        return "".join(source)
    return source or ""


def extract_code_from_notebook(nb_json: dict, target_name: str = "app.py") -> str:
    """Pull Python code out of a Colab/Jupyter notebook.

    Strategy:
      1. Look for a code cell whose first line is '%%writefile <target_name>'.
         If found, return that cell's body verbatim.
      2. Fall back to concatenating all code cells, dropping notebook-only
         lines (`!pip ...`, `%magic`, `from google.colab ...`).
    """
    cells = nb_json.get("cells", [])

    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        src = _flatten_source(cell.get("source"))
        first, _, rest = src.lstrip("\n").partition("\n")
        if first.strip().startswith("%%writefile") and target_name in first:
            return rest.rstrip() + "\n"

    chunks: list[str] = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        src = _flatten_source(cell.get("source"))
        keep_lines: list[str] = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if any(stripped.startswith(p) for p in _DROP_LINE_PREFIXES):
                continue
            keep_lines.append(line)
        if keep_lines:
            chunks.append("\n".join(keep_lines).rstrip())

    if not chunks:
        raise DriveFetchError(
            f"We couldn't find any Python code in that notebook. "
            f"Tip: put your code in a cell starting with '%%writefile {target_name}'."
        )
    return "\n\n".join(chunks).rstrip() + "\n"


# Backwards-compatible alias (unused by main.py after refactor, kept for tests).
def extract_app_py_from_notebook(nb_json: dict) -> str:
    return extract_code_from_notebook(nb_json, target_name="app.py")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_code_from_url(url: str, target_name: str = "app.py") -> FetchedSource:
    """Fetch Python source from a Drive/Colab share link.

    For .ipynb files we try to extract the cell that writes `target_name`;
    otherwise we treat the response as plain Python.
    """
    file_id = extract_file_id(url)
    raw = _fetch_drive_bytes(file_id)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DriveFetchError(
            "We could fetch the file but it isn't UTF-8 text. "
            "Make sure the link points to a .py file or a Colab notebook."
        ) from exc

    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            nb = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            if "cells" in nb:
                code = extract_code_from_notebook(nb, target_name=target_name)
                return FetchedSource(code=code, source_label=f"Colab notebook ({file_id})")

    return FetchedSource(code=text, source_label=f"Drive file ({file_id})")


# Backwards-compatible alias.
def fetch_app_py_from_url(url: str) -> FetchedSource:
    return fetch_code_from_url(url, target_name="app.py")
