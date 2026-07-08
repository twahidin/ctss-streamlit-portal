"""Interactive wizard to generate group login codes + the GROUP_CODES JSON.

Instead of hand-writing the JSON, run this and answer a couple of questions.
It prints:
  1. A single-line GROUP_CODES value ready to paste into Railway.
  2. A readable table of Group / Code / Live URL.
and (optionally) saves both to a gitignored file.

Usage:
    python scripts/make_group_codes.py                 # interactive
    python scripts/make_group_codes.py --groups 7      # skip the prompt
    python scripts/make_group_codes.py --groups 7 --out group_codes.local.txt
    python scripts/make_group_codes.py --groups 7 --json-only   # just the JSON line

Codes are generated with the `secrets` module (cryptographically random) and are
friendly word-style codes like `sky-tiger-42` so students can type them easily.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys

# Word lists for friendly, easy-to-type codes (adjective-animal-number).
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

DEFAULT_URL_TEMPLATE = "https://group-{n}.up.railway.app"


def _make_code(used: set[str]) -> str:
    """Return a unique friendly code like 'sky-tiger-42'."""
    while True:
        code = (
            f"{secrets.choice(ADJECTIVES)}-"
            f"{secrets.choice(ANIMALS)}-"
            f"{secrets.randbelow(90) + 10}"
        )
        if code not in used:
            used.add(code)
            return code


def build_group_codes(n_groups: int, url_template: str) -> dict[str, dict[str, str]]:
    """Build the {code: {id, url, name}} mapping for n groups."""
    used: set[str] = set()
    mapping: dict[str, dict[str, str]] = {}
    for i in range(1, n_groups + 1):
        code = _make_code(used)
        mapping[code] = {
            "id": f"group-{i}",
            "url": url_template.format(n=i),
            "name": f"Group {i}",
        }
    return mapping


def render_table(mapping: dict[str, dict[str, str]]) -> str:
    """Human-readable Group / Code / URL table."""
    rows = [(info["name"], code, info["url"]) for code, info in mapping.items()]
    w_name = max([len("Group")] + [len(r[0]) for r in rows])
    w_code = max([len("Code")] + [len(r[1]) for r in rows])
    lines = [
        f"{'Group'.ljust(w_name)}  |  {'Code'.ljust(w_code)}  |  Live URL",
        f"{'-' * w_name}--+--{'-' * w_code}--+{'-' * 34}",
    ]
    for name, code, url in rows:
        lines.append(f"{name.ljust(w_name)}  |  {code.ljust(w_code)}  |  {url}")
    return "\n".join(lines)


def _prompt_int(msg: str, default: int) -> int:
    raw = input(f"{msg} [{default}]: ").strip()
    if not raw:
        return default
    try:
        val = int(raw)
        if val < 1:
            raise ValueError
        return val
    except ValueError:
        print("  Please enter a whole number of 1 or more.")
        return _prompt_int(msg, default)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate group codes + GROUP_CODES JSON.")
    parser.add_argument("--groups", type=int, help="How many groups (skips the prompt).")
    parser.add_argument(
        "--url-template",
        default=DEFAULT_URL_TEMPLATE,
        help=f"Live URL pattern, use {{n}} for the group number (default: {DEFAULT_URL_TEMPLATE}).",
    )
    parser.add_argument("--out", help="Also save codes + JSON to this file (keep it secret!).")
    parser.add_argument("--json-only", action="store_true", help="Print only the JSON line.")
    args = parser.parse_args()

    n_groups = args.groups if args.groups is not None else _prompt_int("How many groups?", 7)
    if n_groups < 1:
        print("Number of groups must be at least 1.", file=sys.stderr)
        return 1

    mapping = build_group_codes(n_groups, args.url_template)
    json_line = json.dumps(mapping, separators=(",", ":"))

    if args.json_only:
        print(json_line)
        return 0

    table = render_table(mapping)
    print("\n" + table + "\n")
    print("Paste this as the GROUP_CODES variable on your Railway portal service:\n")
    print(json_line + "\n")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("CTSS Streamlit Portal — Group Codes (KEEP SECRET)\n\n")
            fh.write(table + "\n\n")
            fh.write("GROUP_CODES value:\n")
            fh.write(json_line + "\n")
        print(f"Saved to {args.out}  (this file is gitignored — do not share it).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
