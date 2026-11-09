#!/usr/bin/env python3
"""cortex_vault_lint.py: integrity checks for a knowledge vault.

Cross-references the files on disk, `index.md`, `log.md` and wiki links,
then reports drift, orphans, broken links and missing frontmatter. Stdlib only.

    python3 cortex_vault_lint.py <vault> [--no-report]

Prints one JSON object. Writes `<vault>/.lint-report.md` unless --no-report.
Read-only on vault content: that report is the only file it ever writes.
Exit codes: 0 no failing check · 1 a failing check · 2 not a vault directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

WIKI_DIRS = ("wiki/sources", "wiki/concepts", "wiki/entities")
SCHEMA = {
    "wiki/sources": ("title", "source", "type", "date"),
    "wiki/concepts": ("title",),
    "wiki/entities": ("title", "type"),
    "raw/clippings": ("title", "source", "type", "date"),
}
ROOT_MD = {"CLAUDE.md", "README.md", "CONTRIBUTING.md", "index.md", "log.md", ".lint-report.md"}
LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
LOG_SOURCE_RE = re.compile(r"\[\[(wiki/sources/[^\]]+)\]\]")

# (id, title, severity)
CHECKS = (
    ("orphans_on_disk", "Orphans on disk", "FAIL"),
    ("orphans_in_index", "Orphans in index", "FAIL"),
    ("broken_links", "Broken wikilinks", "FAIL"),
    ("frontmatter", "Frontmatter", "FAIL"),
    ("log_drift", "log.md and disk drift", "FAIL"),
    ("outside_canon", "Files outside the canonical structure", "FAIL"),
    ("concept_without_sources", "Concept without ## Sources", "FAIL"),
    ("zero_inbound", "Source or entity with no inbound links", "WARN"),
)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def markdown_in(vault: Path, *dirs: str) -> list[Path]:
    found: list[Path] = []
    for d in dirs:
        found.extend(sorted((vault / d).glob("*.md")))
    return found


def frontmatter(text: str) -> tuple[dict | None, str]:
    """Top-level `key: value` pairs. Enough for required-field checks, no YAML dep."""
    if not text.startswith("---\n"):
        return None, "missing opening ---"
    end = text.find("\n---", 3)
    if end == -1:
        return None, "missing closing ---"
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if match:
            fields[match.group(1)] = match.group(2).strip().strip("\"'")
    return fields, ""


def slug_of(ref: str) -> str:
    return Path(ref.split("|")[0].split("#")[0].strip()).name.removesuffix(".md")


def check_all(vault: Path) -> dict[str, list[str]]:
    index = read(vault / "index.md")
    log = read(vault / "log.md")
    wiki = markdown_in(vault, *WIKI_DIRS)
    raw = markdown_in(vault, "raw/clippings")
    slugs_wiki = {p.stem for p in (vault / "wiki").rglob("*.md")}
    slugs_all = slugs_wiki | {p.stem for p in (vault / "raw").rglob("*.md")}
    out: dict[str, list[str]] = {c[0]: [] for c in CHECKS}

    out["orphans_on_disk"] = [str(p.relative_to(vault)) for p in wiki if p.stem not in index]
    out["orphans_in_index"] = [r for r in LINK_RE.findall(index) if slug_of(r) not in slugs_wiki]
    links: dict[str, set[Path]] = {}
    for path in wiki + raw:
        for ref in LINK_RE.findall(read(path)):
            links.setdefault(slug_of(ref), set()).add(path)
            if slug_of(ref) not in slugs_all:
                out["broken_links"].append(f"{path.relative_to(vault)}: {ref}")

    for prefix, required in SCHEMA.items():
        for path in markdown_in(vault, prefix):
            rel = path.relative_to(vault)
            fields, error = frontmatter(read(path))
            if fields is None:
                out["frontmatter"].append(f"{rel}: {error}")
                continue
            missing = [k for k in required if not fields.get(k)]
            if missing:
                out["frontmatter"].append(f"{rel}: missing {', '.join(missing)}")

    for ref in LOG_SOURCE_RE.findall(log):
        if not ((vault / f"{ref}.md").is_file() or (vault / ref).is_file()):
            out["log_drift"].append(f"row without file: {ref}")
    for path in markdown_in(vault, "wiki/sources"):
        if f"wiki/sources/{path.stem}" not in log:
            out["log_drift"].append(f"file without row: {path.relative_to(vault)}")

    out["outside_canon"] = [f"root: {p.name}" for p in sorted(vault.glob("*.md")) if p.name not in ROOT_MD]
    for top in ("wiki", "raw"):
        for path in (vault / top).rglob("*.md"):
            if len(path.relative_to(vault / top).parts) > 2:
                out["outside_canon"].append(f"depth: {path.relative_to(vault)}")

    out["concept_without_sources"] = [
        str(p.relative_to(vault)) for p in markdown_in(vault, "wiki/concepts") if "## Sources" not in read(p)
    ]

    index_slugs = {slug_of(r) for r in LINK_RE.findall(index)}
    log_slugs = {slug_of(r) for r in LINK_RE.findall(log)}
    for path in markdown_in(vault, "wiki/sources", "wiki/entities"):
        others = links.get(path.stem, set()) - {path}
        if not others and path.stem not in index_slugs and path.stem not in log_slugs:
            out["zero_inbound"].append(str(path.relative_to(vault)))
    return out


def lint(vault: Path) -> dict:
    findings = check_all(vault)
    checks = []
    for check_id, title, severity in CHECKS:
        count = len(findings[check_id])
        checks.append({"id": check_id, "title": title, "status": severity if count else "PASS",
                       "count": count, "findings": findings[check_id]})
    failed = sum(c["count"] for c in checks if c["status"] == "FAIL")
    return {"vault": str(vault), "result": "FAIL" if failed else "PASS", "failed": failed, "checks": checks}


def write_report(vault: Path, report: dict) -> None:
    result = report["result"] if report["result"] == "PASS" else f"FAIL ({report['failed']} issues)"
    lines = ["# Vault Lint Report", "", f"Generated: {datetime.now():%Y-%m-%d %H:%M}", f"Result: {result}", "",
             "## Summary", "| Check | Status | Count |", "|-------|--------|-------|"]
    lines += [f"| {c['title']} | {c['status']} | {c['count']} |" for c in report["checks"]]
    lines += ["", "## Findings"]
    for check in report["checks"]:
        if check["findings"]:
            lines += ["", f"### {check['title']}", *[f"- {f}" for f in check["findings"]]]
    (vault / ".lint-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Integrity checks for a knowledge vault.")
    parser.add_argument("vault")
    parser.add_argument("--no-report", action="store_true", help="do not write .lint-report.md")
    args = parser.parse_args(argv)
    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        print(json.dumps({"vault": str(vault), "error": "not a directory"}))
        return 2
    report = lint(vault)
    if not args.no_report:
        write_report(vault, report)
    print(json.dumps(report, indent=2))
    return 1 if report["result"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
