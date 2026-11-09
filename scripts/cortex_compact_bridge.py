#!/usr/bin/env python3
"""cortex_compact_bridge.py — carry an SDD cycle across the `/compact` boundary.

Wired as a plugin hook (`hooks/hooks.json`). Two events, one script, dispatched
on `hook_event_name` from the stdin payload:

  SessionStart (matcher: compact)  → emit a resume brief as `additionalContext`,
                                     so the model wakes up oriented and the user
                                     never has to type a resume prompt.
  PreCompact   (matcher: auto)     → drop a breadcrumb in the cycle folder.
                                     Never blocks, never writes to stdout.

Why this exists
---------------
After a compaction, the ONLY channel re-injected into context is the cycle's
`CLAUDE.md` and its `@SETUP.md` import. `handoff-*.md`, `EXP.md`, `PLAN.md`, `EXEC.md` and the `.cortex-*.json` receipts stay on
disk but are not reloaded.
This script reads them and hands the model a compact, honest snapshot.

Hard rules (this hook runs in EVERY session on a machine where the plugin is
enabled, whether or not that session has anything to do with a cycle):

- Always exit 0. A non-zero exit or a traceback becomes a visible hook-error
  notice in unrelated sessions.
- Silent no-op (empty stdout) unless the payload's `cwd` is an SDD cycle.
- Stdlib only, no network, no subprocess. Budget: a few milliseconds.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- contract constants -----------------------------------------------------

# Stamp written by cortex_init.py into <cycle>/.claude/settings.json. Its
# presence, next to a CLAUDE.md, is what marks a real cycle.
STAMP_KEY = "cortexCycle"

HANDOFF_RECEIPT = ".cortex-handoff-receipt.json"
COMPACT_LOG = ".cortex-compact-log.json"
EXEC_RECEIPT = ".cortex-exec-receipt.json"
END_RECEIPT = ".cortex-end-receipt.json"

CHAIN_ARTIFACTS = ("EXP.md", "PLAN.md", "EXEC.md", "SESSION-SUMMARY.md", "SETUP.md")

# Handoff sections inlined verbatim. Load-bearing coupling: skills/handoff/SKILL.md
# step 6 mandates these exact `##` headings. Renaming them there breaks the brief.
INLINE_SECTIONS = ("Current Status", "Pending Items", "Next Steps to Continue")

# A handoff older than this is reported as STALE and only pointed at, never
# inlined — priming the model with someone else's turn is worse than silence.
FRESH_WINDOW_SECONDS = 2 * 60 * 60

# Total `additionalContext` budget. A long injection at compact time undoes part
# of the compact it is meant to survive.
BRIEF_BUDGET = 4096

MAX_LOG_EVENTS = 10

# Checkbox grammar + per-phase progress live in cortex_exec_progress.py — the one
# parser every skill's progress number comes from.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
try:
    from cortex_exec_progress import DONE_RE, OPEN_RE, read_progress  # noqa: E402,F401
except ImportError:  # a broken install must still never surface as a hook error
    DONE_RE = re.compile(r"^\s*[-*]\s+\[[xX]\]")
    OPEN_RE = re.compile(r"^\s*[-*]\s+\[\s\]")

    def read_progress(cycle: Path) -> dict:  # flat fallback: counts only, no phases
        try:
            lines = (Path(cycle) / "EXEC.md").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        done = sum(1 for line in lines if DONE_RE.match(line))
        open_ = sum(1 for line in lines if OPEN_RE.match(line))
        return {"done": done, "total": done + open_, "active_phase": None, "phases": [], "phase_count": 0}


# --- tolerant IO ------------------------------------------------------------


def read_payload() -> dict:
    """Parse the hook payload from stdin. Anything unparseable → {}."""
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def load_json(path: Path) -> dict:
    """Read a JSON object from disk. Missing/malformed → {} (never raises)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):  # nested past the limit is malformed too
        return {}
    return data if isinstance(data, dict) else {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def now() -> datetime:
    return datetime.now(timezone.utc)


# --- cycle guard ------------------------------------------------------------


def resolve_cycle(payload: dict) -> tuple[Path | None, dict]:
    """Return (cycle_dir, settings) when the payload's cwd is an SDD cycle.

    A cycle has a `CLAUDE.md` AND a `.claude/settings.json` carrying the
    `cortexCycle` stamp. `cwd` comes from the payload, not os.getcwd():
    the hook process may be spawned elsewhere.
    """
    raw = payload.get("cwd") or os.getcwd()
    try:
        cwd = Path(str(raw)).expanduser()
    except (TypeError, ValueError):
        return None, {}
    if not (cwd / "CLAUDE.md").is_file():
        return None, {}
    settings = load_json(cwd / ".claude" / "settings.json")
    if not settings.get(STAMP_KEY):
        return None, {}
    return cwd, settings


# --- PreCompact branch ------------------------------------------------------


def handle_precompact(cycle: Path, payload: dict) -> None:
    """Append a breadcrumb so the next brief can say 'this compact was automatic'."""
    log_path = cycle / COMPACT_LOG
    existing = load_json(log_path)
    events = existing.get("events")
    if not isinstance(events, list):
        events = []
    events = [e for e in events if isinstance(e, dict)]
    events.append(
        {
            "at": now().isoformat(timespec="seconds"),
            "trigger": payload.get("compaction_trigger") or "auto",
            "transcript_path": payload.get("transcript_path") or None,
            "session_id": payload.get("session_id") or None,
        }
    )
    body = {"schemaVersion": 1, "events": events[-MAX_LOG_EVENTS:]}
    try:
        log_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # a breadcrumb is a nice-to-have; never let it break a compaction


# --- brief building blocks --------------------------------------------------


def age_label(seconds: float) -> str:
    """Human age, coarse on purpose — the point is fresh-vs-old, not precision."""
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{int(seconds)} seconds ago"
    minutes = seconds / 60
    if minutes < 90:
        return f"{int(minutes)} minutes ago"
    hours = minutes / 60
    if hours < 36:
        return f"{int(hours)} hours ago"
    return f"{int(hours / 24)} days ago"


def parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def file_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def resolve_handoff(cycle: Path) -> tuple[Path | None, datetime | None]:
    """Newest handoff for this cycle, plus when it was written.

    Prefers `.cortex-handoff-receipt.json` (written by /cortex:handoff). Falls back
    to the newest `handoff-*.md` by mtime when the receipt is missing or stale.
    """
    receipt = load_json(cycle / HANDOFF_RECEIPT)
    candidate = receipt.get("handoff")
    if isinstance(candidate, str) and candidate.strip():
        path = Path(candidate).expanduser()
        if path.is_file():
            return path, parse_iso(receipt.get("written_at")) or file_mtime(path)

    try:
        candidates = [p for p in cycle.glob("handoff-*.md") if p.is_file()]
        files = sorted(candidates, key=lambda p: p.stat().st_mtime)
    except OSError:
        files = []
    if not files:
        return None, None
    newest = files[-1]
    return newest, file_mtime(newest)


def split_sections(text: str) -> dict:
    """Map top-level `## Heading` → body. Tolerates missing/extra sections."""
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


def clip(body: str, limit: int, path: Path) -> str:
    """Truncate on a line boundary, always naming the file to read in full."""
    if limit <= 0:
        return f"[omitted for length — read {path}]"
    if len(body) <= limit:
        return body
    keep = body[:limit]
    cut = keep.rfind("\n")
    if cut > limit // 2:
        keep = keep[:cut]
    return keep.rstrip() + f"\n… [truncated — read the full file at {path}]"


def chain_lines(cycle: Path) -> list[str]:
    """Cheap on-disk state: same artifacts and receipt keys /cortex:status reads."""
    lines: list[str] = []
    present = [name for name in CHAIN_ARTIFACTS if (cycle / name).is_file()]
    lines.append(
        f"- Artifacts present: {', '.join(present) if present else '(none)'}"
    )

    if (cycle / "EXEC.md").is_file():
        progress = read_progress(cycle)
        done, total = progress["done"], progress["total"]
        if total:
            line = f"- EXEC.md progress: {done}/{total} tasks done, {total - done} open"
            if progress.get("active_phase"):
                line += f" · active: {progress['active_phase']}"
            lines.append(line)
        else:
            lines.append("- EXEC.md progress: no checkboxes found")

    exec_receipt = load_json(cycle / EXEC_RECEIPT)
    if exec_receipt:
        lines.append(f"- exec receipt: status={exec_receipt.get('status', 'unknown')}")

    end_receipt = load_json(cycle / END_RECEIPT)
    if end_receipt:
        lines.append(
            f"- end receipt: closed_at={end_receipt.get('closed_at', 'unknown')}, "
            f"ingested={end_receipt.get('ingested')}"
        )

    if (cycle / "SETUP.md").is_file():
        lines.append(
            "- SETUP.md is already in context via the `@SETUP.md` import — no need to re-read it."
        )
    return lines


def newest_auto_compact(cycle: Path) -> dict | None:
    events = load_json(cycle / COMPACT_LOG).get("events")
    if not isinstance(events, list):
        return None
    autos = [
        e
        for e in events
        if isinstance(e, dict) and (e.get("trigger") or "auto") == "auto"
    ]
    return autos[-1] if autos else None


# --- SessionStart branch ----------------------------------------------------


def build_brief(cycle: Path, settings: dict) -> str:
    header = f"SDD post-compact resume brief — cycle `{cycle.name}`"

    parts: list[str] = [
        header,
        "",
        "The conversation was just compacted. Everything below was read from disk just now,"
        " so it is current even where your recollection is not.",
        "",
        "## Cycle chain",
        *chain_lines(cycle),
        "",
    ]

    handoff, written_at = resolve_handoff(cycle)
    fresh = False
    if handoff is None:
        parts += [
            "## Handoff",
            "None found in this cycle. Reconstruct from the artifacts above —"
            " `/cortex:status` prints the full picture.",
            "",
        ]
    else:
        age_seconds = (now() - written_at).total_seconds() if written_at else None
        fresh = age_seconds is not None and age_seconds < FRESH_WINDOW_SECONDS
        stamp = age_label(age_seconds) if age_seconds is not None else "unknown age"
        verdict = "FRESH" if fresh else "STALE"
        parts += [
            f"## Handoff — `{handoff.name}` (written {stamp} · {verdict})",
            f"Full document: {handoff}",
            "",
        ]
        if not fresh:
            parts += [
                "Not inlined: this handoff is older than the freshness window, so it may"
                " describe a different turn. Read it only after confirming it matches the"
                " task at hand, and verify every claim against the artifacts above.",
                "",
            ]

    footer = [
        "## How to resume",
        "Pick the work back up from the state above. `PLAN.md`, `EXEC.md` and the"
        " `.cortex-*.json` receipts on disk are the source of truth — re-read the ones you"
        " are about to act on, and trust them over any recollection. Do not ask the user to"
        " re-explain the task.",
    ]

    auto = newest_auto_compact(cycle)
    if auto is not None and not fresh:
        note = [
            "## Auto-compact",
            f"This compaction was automatic ({auto.get('at', 'unknown time')}) and no fresh"
            " handoff covers it, so no one froze the turn's decisions before the squash."
            " Treat the artifacts above as the baseline.",
        ]
        transcript = auto.get("transcript_path")
        if isinstance(transcript, str) and transcript.strip():
            note.append(f"Pre-compact transcript, for archaeology: {transcript.strip()}")
        note.append("")
        footer = note + footer

    if not (handoff is not None and fresh):
        return "\n".join(parts + footer)

    # Fresh handoff: inline the mandated sections, sharing the leftover budget.
    sections = split_sections(read_text(handoff))
    available = [
        (name, sections[name]) for name in INLINE_SECTIONS if sections.get(name)
    ]
    if not available:
        parts.append(
            "The handoff has none of the expected sections"
            f" ({', '.join(INLINE_SECTIONS)}) — read the full document."
        )
        parts.append("")
        return "\n".join(parts + footer)

    fixed = len("\n".join(parts + footer))
    overhead = sum(len(f"### {name}\n\n\n") for name, _ in available)
    share = max(0, (BRIEF_BUDGET - fixed - overhead)) // len(available)
    for name, body in available:
        parts += [f"### {name}", clip(body, share, handoff), ""]
    return "\n".join(parts + footer)


def handle_session_start(cycle: Path, settings: dict) -> None:
    brief = build_brief(cycle, settings)
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": brief,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


# --- entrypoint -------------------------------------------------------------


def main() -> None:
    payload = read_payload()
    cycle, settings = resolve_cycle(payload)
    if cycle is None:
        return  # not an SDD cycle — stay completely silent

    # Dispatch on the event, NOT on `source`: hooks.json already scopes this to
    # matcher `compact`, and gating twice would turn a payload-shape change into
    # a silent no-op indistinguishable from "not a cycle".
    event = payload.get("hook_event_name")
    if event == "PreCompact":
        handle_precompact(cycle, payload)
    elif event == "SessionStart":
        handle_session_start(cycle, settings)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — a hook must never surface an error
        pass
    sys.exit(0)
