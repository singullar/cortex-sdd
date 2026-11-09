---
name: status
description: Use when the user wants a quick "where are we?" snapshot of the current cycle — phrases like "cortex:status", "what's the status", "what's the state", "how is the cycle going". Prints a clean, scan-friendly summary of plan progress, PR states, EXEC.md ticks, and pending blockers. Read-only — never modifies anything. No verbosity.
---

# Skill: cortex:status

A quick, read-only "where are we?" snapshot of the current Spec-Driven Development cycle. Lives between explore and end — useful anytime mid-execution to scan progress without re-reading PLAN.md/EXEC.md by hand.

**Strict rule: no verbosity.** Output fits on a screen. No prose paragraphs. No analysis. Just structured facts derived from the artifacts on disk + live PR/state checks where cheap.

## Roles

Lead-only. No subagents, no Team Agents. The status check is small enough that the main context handles it inline.

## Input

Free-text `args`:
- Optional `folder:<name>` prefix (relative to CWD; reject `..` or absolute).
- Remainder ignored.

## Steps

1. **Parse args.** `TARGET_DIR = ${CWD}/${folder}` or `${CWD}`.

2. **Read what exists.** In one pass, check for and read (if present):
   - `EXP.md` → confirm cycle started.
   - `PLAN.md` → extract Objective (1 line), Approach phases list, PR references (`org/repo#num`, full URL form, `gh pr view <num> -R ...`), Dependencies section.
   - `EXEC.md` → run `python3 "${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_exec_progress.py" "$TARGET_DIR" --table --full` and keep its output verbatim for the Progress block (every count on the screen comes from that script — never hand-tallied); `--json` from the same script gives `done`, `total` and `active_phase` for the Delivery/Blockers/Next lines.
   - `.cortex-exec-receipt.json` (if present) → last status, started_at, completed_at, tasks_skipped, `pause`, `review_receipt`.
   - Review receipts (if present) → the file `review_receipt` points to, else `.cortex-review-receipt.json`, plus any `.cortex-review-receipts/*.json` → `status`, `terminal`, `repo`, `pr`.
   - `.cortex-end-receipt.json` (if present) → `closed_at`, `ingested` (bool), `ingested_at` (if present), `vault_paths` (if present). The `ingested` boolean drives the stage decision in step 4.
   - `SESSION-SUMMARY.md` (if present) → already distilled.

3. **Verify PR states** (cheap, parallel). For every unique PR reference parsed from PLAN.md/EXEC.md, plus every PR named by a review receipt, run `gh pr view <num> -R <owner/repo> --json number,state,mergedAt,isDraft,url` in parallel using multiple Bash tool calls in one message. Quote the JSON in your accounting — never assume state. A ref whose call fails with `Could not resolve to a PullRequest` is an issue reference: it is outside the merge gate. Any other `gh` failure is not an issue reference: the PR stays in the merge gate with merged `unknown`. Cap at ~10 PRs; if more, sample the first 10 and note the truncation.

4. **Determine cycle stage:**
   - No EXP.md → `not started`
   - EXP.md only → `explore`
   - EXP.md + PLAN.md + EXEC.md (no exec receipt) → `planned, not started executing`
   - + exec receipt `status: "partial"` → `executing`
   - + exec receipt `status: "complete"` (no end receipt) → `executed, not closed`
   - + end receipt `ingested: false` → `gated, ingest pending` (the cycle passed `/cortex:end`'s gates and the distillation is on disk, but `/cortex:ingest` hasn't run yet)
   - + end receipt `ingested: true` → `closed`
   - Override: if EXEC.md has any `- [ ]` AND PRs in PLAN.md are all merged → flag *"work shipped but EXEC still has open boxes — likely tag/track drift, run /cortex:end to verify"*.

5. **Derive Delivery and Next** from the receipts, the progress script and the `gh` results of step 3, using the five facts and the state table in `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/end/references/delivery-lifecycle.md`. The first rung that matches is Next:
   - End receipt `ingested: true` → owner `none`: closed, nothing owed.
   - End receipt `ingested: false` → owner `session`: `/cortex:ingest`.
   - No EXP.md → `session`: `/cortex:explore`. No PLAN.md or EXEC.md → `session`: `/cortex:plan`.
   - No exec receipt, or its `status` is `partial` → `session`: `/cortex:exec`.
   - Exec receipt `complete` with open EXEC.md boxes → `operator`: finish, tick with evidence, or move to "Removed by replan".
   - A PR with merged `unknown` → `operator`: verify the PR's state with `gh`.
   - Exec complete and no PRs → `session`: `/cortex:end`.
   - Otherwise → the state table's row, per PR.

6. **Print the status block.** Strict format below.

7. **No mutations.** This skill never edits files, opens PRs, modifies state, or invokes other cortex:* skills.

## Output format

```
── cortex status · <TARGET_DIR basename> ──────────

Stage: <stage from step 4>
Objective: <one line from PLAN.md, ≤25 words>

Progress: <X>/<Y> tasks
  Setup     ██       2/2
  Phase 1   ███████  7/7
  Phase 2   █··      1/3
  Phase 3   ·····    0/5
  ...

PRs:
  ✓ org/repo#123  merged 2026-05-28
  ⏳ org/repo#456  open
  ✗ org/repo#789  closed (not merged)
  · org/repo#42   issue (not a PR)

Blockers:
  • <open task with no obvious progress>
  • <open question caveat from PLAN.md>
  • <PR that's open >7 days>

Receipts:
  exec:  partial · started 2026-05-29 14:02 · 2 tasks skipped with caveat
  end:   gated 2026-05-29 16:10 · ingest pending  (or "ingested 2026-05-29 16:30" once closed; "not yet" if absent)

Delivery: executed <done>/<total> · delivered <terminal | partial | aborted | no> · merged <yes | no | unknown> · closed <yes | no>

→ Next (<owner>): <next>
```

**Symbol legend (use exactly these):**
- `✓` complete / merged
- `⏳` in progress / open / active
- `○` pending / not started
- `✗` failed / closed-not-merged / blocked

**Section rules:**
- **Stage** line: always present. One word/phrase.
- **Objective**: from PLAN.md. Skip if no PLAN.md.
- **Progress**: the `cortex_exec_progress.py --table --full` output pasted unmodified (the block above shows its shape — every phase expanded, no ledes) (one bar cell per task, capped at 8). Skip if no EXEC.md. The same renderer draws `/cortex:exec`'s phase-close block, so the two views never disagree.
- **PRs**: from the gh checks in step 3. Skip if zero PRs detected. An issue reference is never a PR row and never a `?` blocker: list it once as `  · org/repo#N  issue (not a PR)` under PRs.
- **Blockers**: skip if none. Otherwise max 5 bullets, 1 line each.
- **Receipts**: skip if no receipts. One line per receipt type.
- **Delivery**: from step 5. Skip until the exec receipt is `complete`. It keeps executed · delivered · merged · closed apart — an open PR is delivered, never "done".
- **Next**: always present. Single sentence with its owner (`operator` = a human acts — merge, settle needs-human findings; `session` = a skill to run here; `none` = nothing owed). Map it by hand with step 5, only from the receipts and `gh`, never from a handoff's Next Steps, so it never re-suggests a push, PR or review that already happened.

## Constraints

- **Read-only.** Never write to any file. Never edit EXEC.md. Never close PRs.
- **No verbosity.** No rationale, no analysis, no "what this means" — just the structured facts. Output fits on ~30 lines including the box border.
- **Verify PR states with `gh`, never assume.** Same rule as cortex:plan/cortex:end — quoted JSON or it doesn't count. If `gh` is unavailable for a particular PR (auth, rate limit), show it as `?` with a one-line reason in Blockers.
- **English output**, regardless of conversation language — consistent with the rest of the lifecycle.
- **No PR detail beyond merged/open/closed.** This skill is a glance, not a review.
- **No mate spawn.** This is a single-context status check; spawning anything would defeat the "quick scan" purpose.
- **Skill never blocks on questions.** If something is ambiguous (e.g., two conflicting cycle states, or a receipt that disagrees with EXEC.md), surface as a Blocker line — do NOT use `AskUserQuestion`.
