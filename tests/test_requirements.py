"""Every third-party import must be declared in requirements.txt.

A missing dependency passes locally — the venv already has it — and only
fails at deploy time. That is exactly how pandas reached production absent:
installed by hand while building projections, never declared.

    .venv/bin/python -m tests.test_requirements
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

STDLIB = {
    "os", "sys", "io", "json", "time", "re", "secrets", "sqlite3", "stat",
    "typing", "pathlib", "unicodedata", "base64", "urllib", "webbrowser",
    "threading", "dataclasses", "collections", "math", "datetime", "ffb",
}

# import name -> distribution name, where they differ
ALIASES = {"dotenv": "python-dotenv", "multipart": "python-multipart"}


def declared():
    out = set()
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=;\[]", line)[0].strip().lower()
        if name:
            out.add(name)
    return out


def imported():
    out = set()
    for path in (ROOT / "ffb").glob("*.py"):
        for line in path.read_text().splitlines():
            m = re.match(r"\s*(?:import|from)\s+([a-z_][a-z0-9_]*)", line)
            if m and m.group(1) not in STDLIB:
                out.add(m.group(1))
    return out


def main():
    have = declared()
    need = {ALIASES.get(n, n) for n in imported()}
    missing = sorted(n for n in need if n.lower() not in have)
    for n in sorted(need):
        print("[{}] {}".format("PASS" if n.lower() in have else "FAIL", n))
    if missing:
        print("\nMISSING from requirements.txt: {}".format(", ".join(missing)))
        return 1
    print("\nall imports declared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
