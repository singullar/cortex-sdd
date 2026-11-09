#!/usr/bin/env python3
"""CI gate: `claude plugin validate` must pass, allowing only the root CLAUDE.md warning."""

import json
import subprocess
import sys

ALLOWED = "CLAUDE.md at the plugin root is not loaded as project context"


def problems(report: dict) -> list[str]:
    found: list[str] = []
    for section in [report.get("manifest") or {}, *(report.get("contents") or [])]:
        found.extend(f"error: {e.get('message')}" for e in section.get("errors") or [])
        found.extend(
            f"warning: {w.get('message')}" for w in section.get("warnings") or []
            if ALLOWED not in str(w.get("message"))
        )
    return found


def main() -> int:
    proc = subprocess.run(["claude", "plugin", "validate", ".", "--json"], capture_output=True, text=True)
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        print(proc.stdout, proc.stderr, sep="\n")
        return 1
    bad = problems(report)
    if not report.get("success"):
        bad.append("validation did not succeed")
    print("\n".join(bad) if bad else "Plugin valid.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
