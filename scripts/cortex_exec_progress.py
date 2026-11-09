#!/usr/bin/env python3
"""cortex_exec_progress.py — the ONE parser/renderer for EXEC.md progress.

Every progress number an cortex skill prints comes from here: `/cortex:exec`'s
phase-close block, `/cortex:status`'s Progress table, the compact bridge's progress
field and the compact bridge's chain line all call `read_progress()` or run
this script. The reason is empirical: in one live cycle the same moment was
narrated as "8 of 35" (handoff), `9` (receipt), "9 de 35" (chat) and `10`
(EXEC.md checkboxes) — counts are derived from the checkboxes at render time,
never narrated.

Stdlib only, read-only, always exit 0, JSON or a rendered table on stdout.

    cortex_exec_progress.py <cycle-dir> --json
    cortex_exec_progress.py <cycle-dir> --table [--full] [--ledes '<json>']

EXEC.md grammar (from skills/plan/SKILL.md's template):

    ## Cross-cutting > Setup           kind = setup     short label "Setup"
    ## Phase N — <name> [(wave)]       kind = phase     short label "Phase N"
    ## Cross-cutting > Wrap up         kind = wrapup    short label "Wrap up"
    ## Cross-cutting > Deferred …      kind = deferred  short label "Deferred"
    any other `## …` with checkboxes   kind = other     short label = heading

Checkbox lines are `- [ ]` (open) / `- [x]` (done) at any indentation. Every
checkbox counts toward the header total — Deferred included — so the total
matches what the compact bridge reports.

Section state: `done` (total > 0, nothing open), `active` (the first section
in document order with an open box), `pending` (open boxes, after the active
one). Sections without checkboxes are omitted.

Table shape (the one the operator asked for — one bar cell per task, capped at
BAR_CAP with proportional fill; every phase after the active one collapses
into a single `Phase N+` row unless --full; closed and active rows may carry a
lede supplied by the caller via --ledes, ≤ LEDE_CHARS):

    Progress: 18/35 tasks
      Setup     ██      2/2
      Phase 1   ██████  6/6   six modules
      Phase 4   █····   1/5   APP_KEY written; service unit in progress
      Phase 5+  ······  0/17

`--ledes` is a JSON object keyed by either the short label ("Phase 1") or the
verbatim heading ("Phase 1 — Modules (wave)"). Unknown keys are ignored.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DONE_RE = re.compile(r"^\s*[-*]\s+\[[xX]\]")
OPEN_RE = re.compile(r"^\s*[-*]\s+\[\s\]")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
PHASE_RE = re.compile(r"^Phase\s+(\d+)\s*(?:[—–-]\s*)?(.*)$")
CROSS_RE = re.compile(r"^Cross-cutting\s*>\s*(.+)$", re.I)

BAR_CAP = 8            # widest bar for an expanded row (one cell per task up to here)
COLLAPSED_BAR = 6      # fixed width of the `Phase N+` row's bar
LEDE_CHARS = 60
DONE_CELL, OPEN_CELL = "█", "·"
NO_HEADING = "(no heading)"  # synthetic section for checkboxes above the first `##` — never reported as the active phase


# --- parsing ----------------------------------------------------------------


def classify(heading: str) -> tuple[str, str, int | None, str]:
    """→ (kind, short_label, phase_number, name)."""
    m = PHASE_RE.match(heading)
    if m:
        return "phase", f"Phase {m.group(1)}", int(m.group(1)), m.group(2).strip()
    c = CROSS_RE.match(heading)
    if c:
        rest = c.group(1).strip()
        low = rest.lower()
        if low.startswith("setup"):
            return "setup", "Setup", None, rest
        if low.startswith("wrap"):
            return "wrapup", "Wrap up", None, rest
        if low.startswith("deferred"):
            return "deferred", "Deferred", None, rest
        return "other", rest[:20], None, rest
    return "other", heading[:20], None, heading


def parse_exec(text: str) -> list[dict]:
    """Sections in document order, each with its checkbox counts."""
    sections: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        h = HEADING_RE.match(line)
        if h:
            kind, short, n, name = classify(h.group(1))
            current = {"label": h.group(1), "short": short, "kind": kind, "n": n, "name": name, "done": 0, "total": 0}
            sections.append(current)
            continue
        if DONE_RE.match(line) or OPEN_RE.match(line):
            if current is None:  # checkbox before any heading — count it under a synthetic section
                current = {"label": NO_HEADING, "short": "Tasks", "kind": "other", "n": None, "name": "", "done": 0, "total": 0}
                sections.append(current)
            current["total"] += 1
            if DONE_RE.match(line):
                current["done"] += 1
    return sections


def read_progress(cycle: Path) -> dict:
    """The dict every consumer reads. Keys `done`/`total`/`active_phase` are the
    contract with the compact bridge; the rest is additive."""
    try:
        text = (Path(cycle) / "EXEC.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    sections = [s for s in parse_exec(text) if s["total"]]
    active = None
    seen_active = False
    for s in sections:
        open_ = s["total"] - s["done"]
        if open_ == 0:
            s["state"] = "done"
        elif not seen_active:
            s["state"] = "active"
            seen_active = True
            active = s["label"] if s["label"] != NO_HEADING else None
        else:
            s["state"] = "pending"
    done = sum(s["done"] for s in sections)
    total = sum(s["total"] for s in sections)
    return {
        "done": done,
        "total": total,
        "active_phase": active,
        "phases": sections,
        "phase_count": sum(1 for s in sections if s["kind"] == "phase"),
    }


# --- rendering --------------------------------------------------------------


def bar(done: int, total: int, width: int | None = None) -> str:
    if total <= 0:
        return ""
    cells = width or min(total, BAR_CAP)
    if done >= total:
        filled = cells
    elif cells == total:
        filled = done
    else:
        filled = round(done / total * cells)
        if done > 0:
            filled = max(1, min(cells - 1, filled))  # any progress shows; never looks complete
    filled = max(0, min(cells, filled))
    return DONE_CELL * filled + OPEN_CELL * (cells - filled)


def lede_for(section: dict, ledes: dict) -> str:
    text = ledes.get(section["short"]) or ledes.get(section["label"]) or ""
    text = " ".join(str(text).split())
    return text if len(text) <= LEDE_CHARS else text[: LEDE_CHARS - 1].rstrip() + "…"


def rows_for(progress: dict, ledes: dict, full: bool) -> list[dict]:
    sections = progress["phases"]
    rows: list[dict] = []
    collapse: list[dict] = []
    seen_active = False
    for s in sections:
        if s["kind"] == "deferred":
            continue
        if not full and seen_active and s["kind"] in ("phase", "wrapup"):
            collapse.append(s)
            continue
        rows.append({"label": s["short"], "done": s["done"], "total": s["total"], "bar": bar(s["done"], s["total"]),
                     "lede": lede_for(s, ledes) if s["state"] in ("done", "active") else ""})
        if s["state"] == "active":
            seen_active = True
    if len(collapse) == 1:
        s = collapse[0]
        rows.append({"label": s["short"], "done": s["done"], "total": s["total"], "bar": bar(s["done"], s["total"]), "lede": ""})
    elif collapse:
        first = next((s for s in collapse if s["kind"] == "phase"), collapse[0])
        done = sum(s["done"] for s in collapse)
        total = sum(s["total"] for s in collapse)
        label = f"{first['short']}+" if first["kind"] == "phase" else first["short"]
        rows.append({"label": label, "done": done, "total": total, "bar": bar(done, total, COLLAPSED_BAR), "lede": ""})
    for s in sections:
        if s["kind"] == "deferred":
            rows.append({"label": s["short"], "done": s["done"], "total": s["total"], "bar": bar(s["done"], s["total"]),
                         "lede": "(deferred, not executed)"})
    return rows


def render_table(progress: dict, ledes: dict | None = None, full: bool = False) -> str:
    if not progress["total"]:
        return "Progress: no tasks found in EXEC.md"
    rows = rows_for(progress, ledes or {}, full)
    for r in rows:
        r["count"] = f"{r['done']}/{r['total']}"
    label_w = max(len(r["label"]) for r in rows)
    bar_w = max(len(r["bar"]) for r in rows)
    count_w = max(len(r["count"]) for r in rows)
    lines = [f"Progress: {progress['done']}/{progress['total']} tasks"]
    for r in rows:
        line = f"  {r['label']:<{label_w}}  {r['bar']:<{bar_w}}  {r['count']:<{count_w}}"
        if r["lede"]:
            line += f"   {r['lede']}"
        lines.append(line.rstrip())
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cycle", nargs="?", default=".", help="cycle folder holding EXEC.md (default: cwd)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="print the progress dict (default)")
    mode.add_argument("--table", action="store_true", help="print the rendered table")
    ap.add_argument("--full", action="store_true", help="table: expand every phase (no `Phase N+` collapse)")
    ap.add_argument("--ledes", default="", help="table: JSON object {label: lede} — ≤60 chars each, closed/active rows only")
    args = ap.parse_args()
    try:
        progress = read_progress(Path(args.cycle).expanduser())
        if args.table:
            ledes: dict = {}
            if args.ledes:
                try:
                    parsed = json.loads(args.ledes)
                    ledes = parsed if isinstance(parsed, dict) else {}
                except ValueError:
                    ledes = {}
            print(render_table(progress, ledes, args.full))
        else:
            print(json.dumps(progress, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 — a progress readout must never traceback
        print(json.dumps({"error": f"{exc.__class__.__name__}: {exc}", "done": 0, "total": 0, "phases": []}))


if __name__ == "__main__":
    main()
    sys.exit(0)
