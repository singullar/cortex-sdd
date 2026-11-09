#!/usr/bin/env python3
"""cortex_init.py: configure the plugin and lay down a cycle folder.

    configure --data-dir D --vault P [--cycles-root R]   write config.json
    init --data-dir D [--project N] [--repos a,b] [--vault P] [--cwd C]
         [--model M] [--effort E]                        stamp a cycle folder

`init` writes a marker-bounded block into <target>/CLAUDE.md, merges the
`cortexCycle` / `cortexAiBrainPath` stamps into <target>/.claude/settings.json,
and seeds SETUP.md. With no project it stamps the current folder, and refuses
to do that inside a git work tree so a cycle block never lands in a source repo.

Prints one JSON object. Exit codes: 0 ok · 2 validation error (JSON `error`).
Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_CYCLES_ROOT = "~/cortex-cycles"
BEGIN = "<!-- cortex-init:begin -->"
END = "<!-- cortex-init:end -->"
STAMPED_NAME = re.compile(r"^\d{6}-\d{4}-")
SCRIPTS = Path(__file__).resolve().parent
SETUP_IMPORT = "@SETUP.md"


class ValidationError(Exception):
    pass


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_config(data_dir: str) -> dict:
    if not data_dir:
        return {}
    try:
        value = json.loads((Path(data_dir).expanduser() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def absolute(raw: str) -> Path:
    return Path(os.path.realpath(Path(raw).expanduser()))


def kebab(name: str) -> str:
    if "/" in name or ".." in name:
        raise ValidationError(f"project name must be a single segment: {name!r}")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValidationError(f"project name has no usable characters: {name!r}")
    return slug


def cycle_name(project: str, now: datetime) -> str:
    slug = kebab(project)
    return slug if STAMPED_NAME.match(slug) else f"{now:%y%m%d-%H%M}-{slug}"


def in_git_work_tree(path: Path) -> bool:
    proc = subprocess.run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
                          capture_output=True, text=True)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def template(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def render_block(vault: str, repos: list[str]) -> str:
    repos_block = ""
    if repos:
        listing = "\n".join(f"- `{repo}`" for repo in repos)
        repos_block = template("session-block-mapped-repos.md.tmpl").replace("{{MAPPED_REPOS_LIST}}", listing)
    brain_block = template("session-block-ai-brain.md.tmpl").replace("{{AI_BRAIN_PATH}}", vault) if vault else ""
    block = template("session-block.md.tmpl")
    return block.replace("{{MAPPED_REPOS_BLOCK}}", repos_block).replace("{{AI_BRAIN_BLOCK}}", brain_block).strip() + "\n"


def write_claude_md(target: Path, block: str) -> None:
    path = target / "CLAUDE.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _: block, text, count=1)
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block
    if not re.search(rf"^{re.escape(SETUP_IMPORT)}\s*$", text, re.M):
        text = text.rstrip() + f"\n\n## Cycle setup\n\n{SETUP_IMPORT}\n"
    atomic_write(path, text)


def merge_settings(target: Path, slug: str, vault: str, extra_dirs: list[str], model: str, effort: str) -> None:
    path = target / ".claude" / "settings.json"
    settings: dict = {}
    if path.is_file():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValidationError(f"malformed {path}: {exc}") from exc
        if not isinstance(settings, dict):
            raise ValidationError(f"malformed {path}: expected an object")
    settings["cortexCycle"] = slug
    if vault:
        settings["cortexAiBrainPath"] = vault
    if model:
        settings["model"] = model
    if effort:
        settings["effortLevel"] = effort
    if extra_dirs:
        permissions = settings.setdefault("permissions", {})
        current = list(permissions.get("additionalDirectories") or [])
        permissions["additionalDirectories"] = current + [d for d in extra_dirs if d not in current]
    atomic_write(path, json.dumps(settings, indent=2) + "\n")


def resolve_repos(raw: str, cwd: Path) -> tuple[list[str], list[str]]:
    repos: list[str] = []
    warnings: list[str] = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        path = Path(item).expanduser()
        resolved = absolute(str(path if path.is_absolute() else cwd / path))
        if resolved.is_dir():
            repos.append(str(resolved))
        else:
            warnings.append(f"repo not found, skipped: {item}")
    return repos, warnings


def cmd_configure(args: argparse.Namespace) -> dict:
    if not args.data_dir:
        raise ValidationError("--data-dir is required")
    vault = absolute(args.vault)
    if not vault.is_dir():
        raise ValidationError(f"vault is not a directory: {vault}")
    previous = read_config(args.data_dir)
    config = {
        "schemaVersion": SCHEMA_VERSION,
        "vault_path": str(vault),
        "cycles_root": str(Path(args.cycles_root or previous.get("cycles_root") or DEFAULT_CYCLES_ROOT).expanduser()),
    }
    atomic_write(Path(args.data_dir).expanduser() / "config.json", json.dumps(config, indent=2) + "\n")
    return config


def cmd_init(args: argparse.Namespace) -> dict:
    config = read_config(args.data_dir)
    cwd = absolute(args.cwd)
    if args.project:
        root = Path(str(config.get("cycles_root") or DEFAULT_CYCLES_ROOT)).expanduser()
        target = root / cycle_name(args.project, datetime.now())
    else:
        if in_git_work_tree(cwd):
            raise ValidationError(f"{cwd} is inside a git repository: pass project:<name> to create a cycle folder")
        target = cwd
    target.mkdir(parents=True, exist_ok=True)
    target = absolute(str(target))

    vault = str(absolute(args.vault)) if args.vault else str(config.get("vault_path") or "")
    warnings: list[str] = []
    if vault and not Path(vault).is_dir():
        warnings.append(f"vault not found, skipped: {vault}")
        vault = ""
    if not vault:
        warnings.append("no vault configured: run /cortex:install to enable the brain")
    repos, repo_warnings = resolve_repos(args.repos, cwd)
    warnings.extend(repo_warnings)

    write_claude_md(target, render_block(vault, repos))
    merge_settings(target, target.name, vault, repos + ([vault] if vault else []), args.model, args.effort)
    setup = target / "SETUP.md"
    if not setup.exists():
        atomic_write(setup, template("setup-block.md.tmpl").replace("{{TS}}", f"{datetime.now():%Y-%m-%d %H:%M}"))
    return {"target_dir": str(target), "cycle": target.name, "vault": vault, "repos": repos,
            "warnings": warnings, "created_cycle_folder": bool(args.project)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure cortex and lay down a cycle folder.")
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("configure")
    configure.add_argument("--data-dir", default="")
    configure.add_argument("--vault", required=True)
    configure.add_argument("--cycles-root", default="")
    init = sub.add_parser("init")
    init.add_argument("--data-dir", default="")
    init.add_argument("--project", default="")
    init.add_argument("--repos", default="")
    init.add_argument("--vault", default="")
    init.add_argument("--cwd", default=os.getcwd())
    init.add_argument("--model", default="")
    init.add_argument("--effort", default="")
    args = parser.parse_args(argv)
    try:
        result = cmd_configure(args) if args.command == "configure" else cmd_init(args)
    except ValidationError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
