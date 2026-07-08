"""Configuration: env loading, group codes, denylists, limits."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Final

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Env-backed settings
# ---------------------------------------------------------------------------

GITHUB_TOKEN: Final[str] = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO: Final[str] = os.getenv("GITHUB_REPO", "")  # e.g. "ctss/ctss-streamlit-projects"

# GROUP_CODES is a JSON string mapping code -> {id, url, name?}
# Example: {"sky-tiger-42": {"id": "group-1", "url": "https://group-1.up.railway.app", "name": "Group 1"}}
_GROUP_CODES_RAW: Final[str] = os.getenv("GROUP_CODES", "{}")


@dataclass(frozen=True)
class GroupInfo:
    code: str
    id: str          # folder name in the mono-repo, e.g. "group-1"
    url: str         # live Railway URL
    name: str        # human-readable label

    @property
    def folder(self) -> str:
        return self.id


def _parse_group_codes(raw: str) -> dict[str, GroupInfo]:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GROUP_CODES env var is not valid JSON: {exc}") from exc

    out: dict[str, GroupInfo] = {}
    for code, info in data.items():
        if not isinstance(info, dict):
            raise RuntimeError(f"GROUP_CODES entry for {code!r} must be an object")
        out[code] = GroupInfo(
            code=code,
            id=info["id"],
            url=info["url"],
            name=info.get("name", info["id"].replace("-", " ").title()),
        )
    return out


GROUP_CODES: Final[dict[str, GroupInfo]] = _parse_group_codes(_GROUP_CODES_RAW)


# ---------------------------------------------------------------------------
# Upload limits
# ---------------------------------------------------------------------------

MAX_PY_FILES: Final[int] = 5                    # app.py + up to 4 feature files
MAX_FEATURE_FILES: Final[int] = 4               # one per group member
MAX_PY_BYTES: Final[int] = 100 * 1024          # 100KB per .py
MAX_REQUIREMENTS_BYTES: Final[int] = 10 * 1024  # 10KB
MAX_REQUIREMENTS_LINES: Final[int] = 30         # max packages
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({".py", ".txt"})
REQUIRED_FILE: Final[str] = "app.py"
REQUIREMENTS_FILE: Final[str] = "requirements.txt"

# Rate limit: minimum seconds between uploads per group
UPLOAD_COOLDOWN_SECONDS: Final[int] = 60


# ---------------------------------------------------------------------------
# Python AST denylist
# ---------------------------------------------------------------------------

# Top-level modules (and any submodule of these) that may not be imported.
DENIED_MODULES: Final[frozenset[str]] = frozenset({
    "subprocess",
    "socket",
    "paramiko",
    "pty",
    "ctypes",
    "marshal",
    "shutil",       # rm -rf risk
    "pickle",       # pickle.loads RCE
    "multiprocessing",
    "asyncio.subprocess",
    "telnetlib",
    "ftplib",
    "smtplib",
})

# `from X import Y` where (X, Y) matches — blocks targeted dangerous imports.
DENIED_FROM_IMPORTS: Final[frozenset[tuple[str, str]]] = frozenset({
    ("os", "system"),
    ("os", "popen"),
    ("os", "exec"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "spawn"),
    ("os", "spawnv"),
    ("os", "fork"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "removedirs"),
    ("pickle", "loads"),
    ("pickle", "load"),
    ("marshal", "loads"),
    ("marshal", "load"),
})

# Disallowed bare builtin calls.
DENIED_CALLS: Final[frozenset[str]] = frozenset({
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
    "input",  # not dangerous, but noisy in deployed Streamlit
})

# Dangerous attribute accesses anywhere in the AST.
DENIED_ATTRIBUTES: Final[frozenset[str]] = frozenset({
    "__globals__",
    "__builtins__",
    "__subclasses__",
    "__import__",
    "__loader__",
    "__code__",
    "__class__",
    "f_globals",
    "f_locals",
    "f_back",
    "gi_frame",
})

# Attribute calls of the form `os.system(...)`, `os.popen(...)`, etc.
DENIED_ATTR_CALLS: Final[frozenset[tuple[str, str]]] = frozenset({
    ("os", "system"),
    ("os", "popen"),
    ("os", "exec"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "spawn"),
    ("os", "spawnv"),
    ("os", "fork"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("pickle", "loads"),
    ("pickle", "load"),
    ("marshal", "loads"),
    ("marshal", "load"),
})


# ---------------------------------------------------------------------------
# requirements.txt denylist
# ---------------------------------------------------------------------------

# Block packages that can be misused for RCE / network shenanigans.
BLOCKED_PACKAGES: Final[frozenset[str]] = frozenset({
    "paramiko",
    "fabric",
    "pexpect",
    "pwntools",
    "ptyprocess",
    "ctypes-callable",
    "subprocess32",
})


# ---------------------------------------------------------------------------
# Curated library picker — shown as checkboxes on the upload page.
# `name` is the exact PyPI package name (becomes the requirements.txt entry).
# `label` is what students see. Order within a category controls UI order.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CuratedLibrary:
    name: str
    label: str
    category: str
    default: bool = False
    locked: bool = False  # checkbox cannot be unchecked (e.g. streamlit)


CURATED_LIBRARIES: Final[tuple[CuratedLibrary, ...]] = (
    # Core (always on)
    CuratedLibrary("streamlit", "streamlit", "Core", default=True, locked=True),

    # Data wrangling
    CuratedLibrary("pandas",   "pandas",                 "Data"),
    CuratedLibrary("numpy",    "numpy",                  "Data"),
    CuratedLibrary("openpyxl", "openpyxl (Excel files)", "Data"),

    # Visualisation
    CuratedLibrary("matplotlib", "matplotlib", "Visualisation"),
    CuratedLibrary("seaborn",    "seaborn",    "Visualisation"),
    CuratedLibrary("plotly",     "plotly",     "Visualisation"),
    CuratedLibrary("altair",     "altair",     "Visualisation"),
    CuratedLibrary("pydeck",     "pydeck (maps)", "Visualisation"),

    # AI / LLM
    CuratedLibrary("anthropic",      "anthropic (Claude API)", "AI / LLM"),
    CuratedLibrary("openai",         "openai",                 "AI / LLM"),
    CuratedLibrary("google-genai",   "google-genai (Gemini)",  "AI / LLM"),
    CuratedLibrary("langchain",      "langchain",              "AI / LLM"),
    CuratedLibrary("tiktoken",       "tiktoken (token counts)", "AI / LLM"),

    # Machine learning
    CuratedLibrary("scikit-learn", "scikit-learn", "Machine learning"),

    # Web & I/O
    CuratedLibrary("requests",        "requests",        "Web & I/O"),
    CuratedLibrary("httpx",           "httpx",           "Web & I/O"),
    CuratedLibrary("beautifulsoup4",  "beautifulsoup4",  "Web & I/O"),
    CuratedLibrary("python-dotenv",   "python-dotenv",   "Web & I/O"),

    # Images
    CuratedLibrary("pillow", "pillow (images)", "Images"),

    # Streamlit ecosystem
    CuratedLibrary("streamlit-extras",   "streamlit-extras",          "Streamlit add-ons"),
    CuratedLibrary("streamlit-aggrid",   "streamlit-aggrid (tables)", "Streamlit add-ons"),
    CuratedLibrary("streamlit-folium",   "streamlit-folium (maps)",   "Streamlit add-ons"),
    CuratedLibrary("streamlit-option-menu", "streamlit-option-menu",  "Streamlit add-ons"),
)


def curated_libraries_grouped() -> dict[str, list[CuratedLibrary]]:
    """Return the curated list grouped by category, preserving declaration order."""
    out: dict[str, list[CuratedLibrary]] = {}
    for lib in CURATED_LIBRARIES:
        out.setdefault(lib.category, []).append(lib)
    return out


def known_library_names() -> frozenset[str]:
    return frozenset(lib.name for lib in CURATED_LIBRARIES)


def is_configured() -> bool:
    """True iff the portal has the env vars it needs to actually commit."""
    return bool(GITHUB_TOKEN and GITHUB_REPO and GROUP_CODES)
