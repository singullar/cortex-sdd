#!/usr/bin/env python3
"""cortex_doctor.py: is this machine ready to run the cortex flow?

    python3 cortex_doctor.py --data-dir <dir> [--prereqs-only]

Prints one JSON object: `checks` (id, status PASS|WARN|FAIL, detail, fix) and
`summary`. Stdlib only and read-only: it never changes anything it checks.
`--prereqs-only` skips everything that needs an installed vault, which is what
/cortex:install runs before creating one.

Exit codes: 0 no FAIL · 1 at least one FAIL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cortex_vault_lint  # noqa: E402

MIN_CLAUDE = (2, 1, 275)
MIN_PYTHON = (3, 9)
MIN_GIT = (2, 22)
PLUGIN_ID = "cortex@cortex-sdd"
VAULT_FILES = ("CLAUDE.md", "index.md", "log.md", ".rules/ingestion.md")


def run(*cmd: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


def version_tuple(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(g) for g in match.groups(default="0")) if match else ()


def dotted(version: tuple[int, ...]) -> str:
    return ".".join(str(n) for n in version)


def check(check_id: str, status: str, detail: str, fix: str = "") -> dict:
    return {"id": check_id, "status": status, "detail": detail, "fix": fix}


def check_claude() -> list[dict]:
    proc = run("claude", "--version")
    version = version_tuple(proc.stdout) if proc and proc.returncode == 0 else ()
    if not version:
        return [check("claude-version", "FAIL", "claude not found on PATH", "Install Claude Code."),
                check("plugin-enabled", "FAIL", "cannot query plugins without claude", "Install Claude Code.")]
    results = [
        check("claude-version", "PASS", dotted(version))
        if version >= MIN_CLAUDE else
        check("claude-version", "FAIL", f"{dotted(version)} is older than {dotted(MIN_CLAUDE)}", "Update Claude Code.")
    ]
    listing = run("claude", "plugin", "list", "--json")
    try:
        plugins = json.loads(listing.stdout) if listing else []
    except ValueError:
        plugins = []
    enabled = {p.get("id"): p.get("enabled") for p in plugins if isinstance(p, dict)}
    if enabled.get(PLUGIN_ID):
        results.append(check("plugin-enabled", "PASS", PLUGIN_ID))
    elif PLUGIN_ID in enabled:
        results.append(check("plugin-enabled", "FAIL", "installed but disabled", f"claude plugin enable {PLUGIN_ID}"))
    else:
        results.append(check("plugin-enabled", "FAIL", "not installed", "/plugin install cortex --marketplace singullar/cortex-sdd"))
    review = [pid for pid, on in enabled.items() if on and str(pid).startswith("pr-review-toolkit@")]
    results.append(
        check("pr-review-toolkit", "PASS", review[0]) if review else
        check("pr-review-toolkit", "WARN", "not installed; reviewers fall back to general-purpose agents",
              "Optional: /plugin install pr-review-toolkit@claude-plugins-official")
    )
    return results


def check_python() -> dict:
    version = tuple(sys.version_info[:3])
    if version[:2] >= MIN_PYTHON:
        return check("python", "PASS", dotted(version))
    return check("python", "FAIL", f"{dotted(version)} is older than {dotted(MIN_PYTHON)}", "Install a newer Python 3.")


def check_git() -> dict:
    proc = run("git", "--version")
    version = version_tuple(proc.stdout) if proc and proc.returncode == 0 else ()
    if not version:
        return check("git", "FAIL", "git not found on PATH", "Install git.")
    if version[:2] >= MIN_GIT:
        return check("git", "PASS", dotted(version))
    return check("git", "FAIL", f"{dotted(version)} is older than {dotted(MIN_GIT)}", "Upgrade git.")


def check_gh() -> dict:
    if not shutil.which("gh"):
        return check("gh", "FAIL", "gh not found on PATH", "Install the GitHub CLI: https://cli.github.com")
    proc = run("gh", "auth", "status")
    if proc and proc.returncode == 0:
        return check("gh", "PASS", "authenticated")
    return check("gh", "FAIL", "not authenticated", "gh auth login")


def check_dev_override() -> dict | None:
    value = os.environ.get("CORTEX_DIR")
    if not value:
        return None
    return check("cortex-dir", "WARN", f"CORTEX_DIR overrides the installed plugin: {value}", "Unset it unless you are developing the plugin.")


def load_config(data_dir: str) -> tuple[dict | None, dict]:
    path = Path(data_dir).expanduser() / "config.json" if data_dir else None
    if path is None or not path.is_file():
        return None, check("config", "FAIL", "no configuration found", "Run /cortex:install.")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, check("config", "FAIL", f"unreadable config: {exc}", "Run /cortex:install.")
    if not isinstance(config, dict) or not config.get("vault_path"):
        return None, check("config", "FAIL", "config has no vault_path", "Run /cortex:install.")
    return config, check("config", "PASS", str(path))


def check_vault(vault_path: str) -> list[dict]:
    vault = Path(vault_path).expanduser()
    missing = [name for name in VAULT_FILES if not (vault / name).is_file()]
    if not vault.is_dir() or missing:
        detail = f"{vault}: not a directory" if not vault.is_dir() else f"{vault}: missing {', '.join(missing)}"
        return [check("vault-shape", "FAIL", detail, "Re-clone your vault or fix its path with /cortex:install.")]
    results = [check("vault-shape", "PASS", str(vault))]
    inside = run("git", "-C", str(vault), "rev-parse", "--is-inside-work-tree")
    if not inside or inside.returncode != 0:
        results.append(check("vault-git", "WARN", "not a git repository; ingests are written in place",
                             f"git -C {vault} init"))
    else:
        results.append(check("vault-git", "PASS", "git repository"))
        remote = run("git", "-C", str(vault), "remote")
        if remote and remote.stdout.strip():
            results.append(check("vault-remote", "PASS", remote.stdout.split()[0]))
        else:
            results.append(check("vault-remote", "WARN", "no remote; ingests stay on this machine",
                                 "Add a private remote to your vault."))
    report = cortex_vault_lint.lint(vault.resolve())
    if report["result"] == "PASS":
        results.append(check("vault-lint", "PASS", "no failing check"))
    else:
        results.append(check("vault-lint", "WARN", f"{report['failed']} issue(s) found",
                             f"python3 scripts/cortex_vault_lint.py {vault}"))
    return results


def diagnose(data_dir: str, prereqs_only: bool) -> list[dict]:
    results = [*check_claude(), check_python(), check_git(), check_gh()]
    override = check_dev_override()
    if override:
        results.append(override)
    if prereqs_only:
        return results
    config, config_check = load_config(data_dir)
    results.append(config_check)
    if config:
        results.extend(check_vault(str(config["vault_path"])))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that the cortex flow can run here.")
    parser.add_argument("--data-dir", default="", help="plugin data directory holding config.json")
    parser.add_argument("--prereqs-only", action="store_true", help="skip the checks that need a vault")
    args = parser.parse_args(argv)
    results = diagnose(args.data_dir, args.prereqs_only)
    summary = {s: sum(r["status"] == s for r in results) for s in ("PASS", "WARN", "FAIL")}
    print(json.dumps({"checks": results, "summary": summary}, indent=2))
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
