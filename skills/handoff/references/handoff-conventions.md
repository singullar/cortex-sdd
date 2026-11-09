# Handoff conventions — the shared contract

Consumed inline (read this file, then apply it — never `Skill()`) by **two authors**:

- `/cortex:handoff` — the manual checkpoint.
- `/cortex:exec` — the **phase-close checkpoint** it writes at every `## Phase N` completion of a ≥2-phase plan (§ 4 below).

`<PLUGIN_ROOT>` is the plugin root, resolved by the skill that loaded this file.

Both write the same file shape and the same receipt, so the compact bridge (`<PLUGIN_ROOT>/scripts/cortex_compact_bridge.py`) never has to know who wrote a handoff. Neither author may delegate the write: a subagent does not inherit the conversation and cannot author a faithful handoff; `Skill(cortex:handoff)` mid-run has no return semantics and kills the calling skill.

## 1. Files

| Mode | File | Written by | Overwrite? |
|---|---|---|---|
| checkpoint | `<TARGET_DIR>/handoff-<TS>.md` | `/cortex:handoff` | never — `-2`, `-3` … suffix when the minute collides |
| phase-close | `<TARGET_DIR>/handoff-<TS>.md` | `/cortex:exec` at a phase completion | same suffix rule |

`TS` = `date +%Y-%m-%d-%H%M` (minute resolution, local time) — run at the write, like the receipt's `written_at` (`date -u +%Y-%m-%dT%H:%M:%SZ`); never composed from memory or the conversation. **Never write `.cortex-compact-log.json`** — owned by the bridge's `PreCompact` branch.

## 2. The seven sections — load-bearing headings

English-only body regardless of the conversation language. Exactly these top-level `##` headings, in this order:

- `## Activity Understanding` — what this activity is, what the turn/phase was trying to accomplish, the working context (project, repo, environment).
- `## Current Status` — where we stopped, what is in progress, what is blocked. State plainly; no hedging. **The first paragraph is what the post-compact brief leads with** — make it a complete sentence with the derived count (`**18 of 35 tasks.** Phase 4 in progress …`).
- `## Key Decisions` — choices made with one-line rationales, constraints discovered. Technical adjustments of the turn/phase are listed one line each (`<PLUGIN_ROOT>/skills/exec/SKILL.md` § "When evidence contradicts the plan") — the exec receipt holds the evidence, the line points at it.
- `## Generated Files` — absolute paths of files created or edited **this turn/phase** AND the cortex artifacts to re-read, grouped (a) produced/edited now, (b) pre-existing artifacts (`EXP.md`, `PLAN.md`, `EXEC.md`, `SESSION-SUMMARY.md`, `SOLUTION-OVERVIEW.md`, receipts, `CLAUDE.md`, `.claude/settings.json`, `SETUP.md` with the note "auto-reloads after `/compact` via `@SETUP.md`", earlier `handoff-*.md`). One line per path; never duplicate content. **Never leave this section empty** — a phase that produced nothing on disk still lists group (b).
- `## Pending Items` — what is still open, ordered by priority, one line each. A material divergence still waiting on the operator always comes first (`BLOCKING — waits on the operator: <its question>`); writing a handoff while one is open is always allowed and never resolves it.
- `## Next Steps to Continue` — concrete, copy-pasteable instructions for the next turn to resume cold. A focus hint from args is woven in here.
- `## Suggested Skills` — the skills that help next, one line each.

`<PLUGIN_ROOT>/scripts/cortex_compact_bridge.py` parses `Current Status`, `Pending Items` and `Next Steps to Continue` **verbatim** to build the post-compact brief (shared budget ≈ 4 KB, split equally across the three, truncated on a line boundary). Renaming or reordering a heading silently degrades the brief to a bare pointer. Put the sentence before the table in `Current Status` — truncation eats the tail.

## 3. The receipt — `<TARGET_DIR>/.cortex-handoff-receipt.json`

The pointer the bridge reads so it never guesses which handoff is current. **Overwrite on every write — latest handoff wins**, whichever author wrote it.

```json
{
  "schemaVersion": 1,
  "ts": "<TS>",
  "written_at": "<ISO-8601 UTC, e.g. date -u +%Y-%m-%dT%H:%M:%SZ>",
  "kind": "checkpoint | phase-close",
  "phase": "<verbatim `## Phase N — <name>` heading the checkpoint closed, or null>",
  "handoff": "<absolute path of the file just written>",
  "cycle_dir": "<absolute TARGET_DIR>",
  "focus_hint": "<focus hint from args, or null>",
  "artifacts": ["<absolute paths of the cortex artifacts found in TARGET_DIR>"]
}
```

- `kind` / `phase` are the only fields that differ between authors: `/cortex:handoff` writes `checkpoint` with `phase: null`; `/cortex:exec` writes `phase-close` with the phase label. Readers treat a missing `kind` as `checkpoint`.
- **`$(pwd)` copy rule.** When `TARGET_DIR` ≠ `$(pwd)` and `$(pwd)` is itself a scaffolded cycle (`CLAUDE.md` + `.claude/settings.json`), write a byte-identical copy of the receipt in `$(pwd)` too — the hook resolves from the session's cwd. Identical means `handoff` and `cycle_dir` keep pointing at `TARGET_DIR`; never "fix up" the copy.

## 4. Phase-close checkpoints (second author: `/cortex:exec`)

Written by the exec loop's **phase close** (`<PLUGIN_ROOT>/skills/exec/SKILL.md` § "Phase close") at every `## Phase N` completion when EXEC.md holds ≥2 `## Phase N` headings. Same seven sections, same receipt, plus:

- `## Current Status` = **one bold lede sentence first** (`**<done> of <total> tasks.** Phase N closed — <what it delivered>; Phase N+1 <starts now | is the first apply | …>.`), then the progress table printed **verbatim from** `python3 <PLUGIN_ROOT>/scripts/cortex_exec_progress.py "<TARGET_DIR>" --table --ledes '<json>'`. Counts are never typed by hand.
- `## Generated Files` group (a) = the files this phase's tasks touched (from the owners' reports and the lead's own edits, absolute paths) plus the commit SHA when the phase was committed.
- `## Pending Items` = the remaining open tasks by phase (counts from the script), the unverified assumptions pinned to later phases in PLAN.md's Caveats, and anything the phase surfaced but did not resolve.
- `## Next Steps to Continue` opens with `Continue /cortex:exec at <next phase label> — <its Mates: shape>`.
- No `/compact` line is printed and the loop does not pause — the checkpoint exists so an automatic compaction mid-phase is safe (the bridge re-injects it); the operator compacts when they choose.
- The exec receipt gets the checkpoint appended to its `checkpoints` list; see `<PLUGIN_ROOT>/skills/exec/SKILL.md` § "Phase close".

## 5. Hygiene (both authors)

- **Redact secrets.** Names and identifiers yes, credential values never.
- **Never duplicate other artifacts** — reference `PLAN.md`, `EXP.md`, `EXEC.md`, ADRs and PR descriptions by absolute path or URL.
- **TARGET_DIR boundary.** Resolved from `pwd` → never write outside it. Explicit absolute path from the operator → trust it.
- **No "Team Mates Status" section.** Inline subagents have already returned; their results live in the artifacts and the exec receipt's `spawn_fallbacks`.
- **Lead-only.** No `Agent()`, no `TeamCreate`, no `Skill()`.
