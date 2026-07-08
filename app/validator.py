"""AST-based safety validation for student-submitted Python files.

Three entry points:
    validate_python_file(filename, content) -> (ok, errors)
    validate_requirements(content)          -> (ok, errors)
    validate_upload_bundle(files)           -> (ok, errors)
"""

from __future__ import annotations

import ast
import re

from . import config


# ---------------------------------------------------------------------------
# Python file validation
# ---------------------------------------------------------------------------

def _module_is_denied(module_name: str) -> bool:
    """True if `module_name` (or any prefix) is in the denied module list."""
    parts = module_name.split(".")
    for i in range(1, len(parts) + 1):
        prefix = ".".join(parts[:i])
        if prefix in config.DENIED_MODULES:
            return True
    return False


class _SafetyVisitor(ast.NodeVisitor):
    """Walk the AST and collect every safety violation we find."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    # `import foo` / `import foo.bar`
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _module_is_denied(alias.name):
                self.errors.append(
                    f"Line {node.lineno}: importing '{alias.name}' is not allowed."
                )
        self.generic_visit(node)

    # `from foo import bar`
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module and _module_is_denied(module):
            self.errors.append(
                f"Line {node.lineno}: importing from '{module}' is not allowed."
            )
        for alias in node.names:
            if (module, alias.name) in config.DENIED_FROM_IMPORTS:
                self.errors.append(
                    f"Line {node.lineno}: 'from {module} import {alias.name}' is not allowed."
                )
        self.generic_visit(node)

    # Calls: `eval(...)`, `os.system(...)`, etc.
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in config.DENIED_CALLS:
            self.errors.append(
                f"Line {node.lineno}: calling '{func.id}()' is not allowed."
            )
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            pair = (func.value.id, func.attr)
            if pair in config.DENIED_ATTR_CALLS:
                self.errors.append(
                    f"Line {node.lineno}: calling '{func.value.id}.{func.attr}()' is not allowed."
                )
        self.generic_visit(node)

    # Attribute access: `something.__globals__`, etc.
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in config.DENIED_ATTRIBUTES:
            self.errors.append(
                f"Line {node.lineno}: accessing '.{node.attr}' is not allowed."
            )
        self.generic_visit(node)


def validate_python_file(filename: str, content: str) -> tuple[bool, list[str]]:
    """Parse the file with `ast` and reject any node matching the denylist."""
    errors: list[str] = []

    if len(content.encode("utf-8")) > config.MAX_PY_BYTES:
        errors.append(
            f"{filename} is larger than {config.MAX_PY_BYTES // 1024}KB."
        )
        return False, errors

    try:
        tree = ast.parse(content, filename=filename)
    except SyntaxError as exc:
        errors.append(f"{filename} has a syntax error on line {exc.lineno}: {exc.msg}")
        return False, errors

    visitor = _SafetyVisitor()
    visitor.visit(tree)

    if visitor.errors:
        prefixed = [f"{filename}: {msg}" for msg in visitor.errors]
        return False, prefixed
    return True, []


# ---------------------------------------------------------------------------
# requirements.txt validation
# ---------------------------------------------------------------------------

# A liberal but strict-enough match for "name[extras]==version" style lines.
# Rejects anything containing '/', '@', 'git+', 'file:', etc.
_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"           # package name
    r"(\[[A-Za-z0-9_,\-\s]+\])?"             # optional extras
    r"\s*"
    r"([<>=!~]=?\s*[A-Za-z0-9._*+\-]+(\s*,\s*[<>=!~]=?\s*[A-Za-z0-9._*+\-]+)*)?"
    r"\s*(?:#.*)?$"
)


def _normalize_name(line: str) -> str:
    """Extract the lowercase package name from a requirement line."""
    name = re.split(r"[\[<>=!~;# ]", line, maxsplit=1)[0]
    return name.strip().lower().replace("_", "-")


def validate_requirements(content: str) -> tuple[bool, list[str]]:
    errors: list[str] = []

    if len(content.encode("utf-8")) > config.MAX_REQUIREMENTS_BYTES:
        errors.append(
            f"requirements.txt is larger than {config.MAX_REQUIREMENTS_BYTES // 1024}KB."
        )

    package_count = 0
    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Reject VCS/file/URL specs outright.
        lowered = line.lower()
        if any(token in lowered for token in ("git+", "://", "file:", "@")):
            errors.append(
                f"requirements.txt line {lineno}: only PyPI packages are allowed (no git/file URLs)."
            )
            continue

        # Reject `-e` editable installs and other flags.
        if line.startswith("-"):
            errors.append(
                f"requirements.txt line {lineno}: flags like '{line.split()[0]}' are not allowed."
            )
            continue

        if not _REQUIREMENT_RE.match(line):
            errors.append(
                f"requirements.txt line {lineno}: '{line}' is not a valid requirement."
            )
            continue

        name = _normalize_name(line)
        if name in config.BLOCKED_PACKAGES:
            errors.append(
                f"requirements.txt line {lineno}: package '{name}' is not allowed."
            )
            continue

        package_count += 1

    if package_count > config.MAX_REQUIREMENTS_LINES:
        errors.append(
            f"requirements.txt has {package_count} packages; the limit is "
            f"{config.MAX_REQUIREMENTS_LINES}."
        )

    return (not errors), errors


# ---------------------------------------------------------------------------
# Bundle-level checks
# ---------------------------------------------------------------------------

def validate_upload_bundle(files: dict[str, str]) -> tuple[bool, list[str]]:
    """
    `files` maps filename -> textual content.
    Enforces:
      - `app.py` is present
      - at most 3 .py files and 1 requirements.txt
      - per-file checks via validate_python_file / validate_requirements
    """
    errors: list[str] = []

    if config.REQUIRED_FILE not in files:
        errors.append("Your upload must include an 'app.py' file.")

    py_files = [name for name in files if name.endswith(".py")]
    if len(py_files) > config.MAX_PY_FILES:
        errors.append(
            f"You uploaded {len(py_files)} .py files; the limit is {config.MAX_PY_FILES}."
        )

    extra_files = [
        name for name in files
        if not name.endswith(".py") and name != config.REQUIREMENTS_FILE
    ]
    if extra_files:
        errors.append(
            "Only .py files and requirements.txt are accepted. "
            f"Unexpected: {', '.join(extra_files)}"
        )

    # Filename safety: no path separators, no leading dots.
    for name in files:
        if "/" in name or "\\" in name or name.startswith("."):
            errors.append(f"Invalid filename: {name!r}")

    if errors:
        return False, errors

    # Per-file content checks.
    for name, content in files.items():
        if name.endswith(".py"):
            ok, file_errors = validate_python_file(name, content)
            if not ok:
                errors.extend(file_errors)
        elif name == config.REQUIREMENTS_FILE:
            ok, req_errors = validate_requirements(content)
            if not ok:
                errors.extend(req_errors)

    return (not errors), errors


# ---------------------------------------------------------------------------
# Inline smoke tests — run with `python -m app.validator`
# ---------------------------------------------------------------------------

def _run_smoke_tests() -> None:
    safe = "import streamlit as st\nst.title('hi')\n"
    ok, errs = validate_python_file("app.py", safe)
    assert ok, errs

    bad_cases = [
        "import os\nos.system('rm -rf /')\n",
        "import subprocess\nsubprocess.run(['ls'])\n",
        "eval('1+1')\n",
        "exec('print(1)')\n",
        "from os import system\n",
        "x = (1).__class__.__bases__[0].__subclasses__()\n",
        "import pickle\npickle.loads(b'')\n",
        "__import__('os').system('ls')\n",
    ]
    for src in bad_cases:
        ok, errs = validate_python_file("bad.py", src)
        assert not ok, f"Should have rejected: {src!r}"

    ok, errs = validate_requirements("streamlit\npandas==2.0\n")
    assert ok, errs

    bad_reqs = [
        "git+https://github.com/x/y.git",
        "paramiko",
        "-e .",
        "some package with spaces",
    ]
    for line in bad_reqs:
        ok, errs = validate_requirements(line)
        assert not ok, f"Should have rejected: {line!r}"

    print("validator smoke tests passed")


if __name__ == "__main__":
    _run_smoke_tests()
