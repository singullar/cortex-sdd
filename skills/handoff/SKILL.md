---
name: handoff
description: >-
  Use when closing the current turn and you want a handoff document plus a follow-up `/compact` — phrases like "cortex:handoff", "do the handoff", "hand off to the next turn", "close this turn with a handoff", "wrap and compact". Writes `handoff-YYYY-MM-DD-HHMM.md` (a mid-session checkpoint) to the current directory (or an explicit TARGET_DIR passed via args) capturing activity understanding, current status, key decisions, generated files, pending items, and next steps for the next turn to resume from cold, plus a `.cortex-handoff-receipt.json` pointer; after saving, prints a ready-to-paste `/compact` command — the skill does NOT invoke the built-in itself (Claude Code intercepts built-in slash commands at the CLI input layer, so no tool can dispatch them). No resume prompt is needed after the compact: the plugin's compact-bridge hook re-injects the handoff automatically. (`/cortex:exec` writes the same checkpoint by itself at every phase close of a ≥2-phase plan — no need to invoke this skill between phases.) Requires the cwd to be a `cortex:init` project (presence of `CLAUDE.md` AND `.claude/settings.json`) unless an explicit absolute TARGET_DIR is passed via args.
---

# Skill: cortex:handoff

Captures the current turn into a structured handoff Markdown and stages a follow-up `/compact` for the user to dispatch. The point is to leave the next turn (or another agent) able to pick up cold, without re-reading the full transcript.

**This skill is a wrapper for `/handoff` + `/compact` with a fixed file path/name convention.** It does NOT chain the two built-ins programmatically — see "How `/compact` is handled" below.

**Plugin root:** `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}`, written `<PLUGIN_ROOT>` in this skill's references.

## Roles

Lead-only. The model running the skill writes the file directly from the current conversation context. **No subagents** — spawned agents do not inherit the conversation transcript, so they cannot author a faithful handoff. No Team Agents.

## Input

Free-text `args`, all optional:

- **Absolute path** (starts with `/` or `~`) → used as `TARGET_DIR` for the handoff file. Bypasses the cortex:init project guard.
- **Anything else** → treated as a focus hint for the next turn; surfaces verbatim in the `Next Steps to Continue` section.
- **No args** → `TARGET_DIR = $(pwd)`, no extra focus hint, skill synthesizes everything from the live conversation.

Args may combine: e.g. `/cortex:handoff ~/scratch/handoffs focus on the kafka retry refactor` — the first token resolves to TARGET_DIR if it parses as an absolute path; the remainder becomes the focus hint.

## Two authors, one file shape

- **Checkpoint (this skill).** A mid-session snapshot before a `/compact` — context-limit hygiene. Many can accumulate per cycle (`handoff-*.md`); each is cheap and disposable.
- **Phase-close (not this skill's).** `/cortex:exec` writes the same checkpoint file + receipt (`kind: "phase-close"`, `phase: <heading>`) at every `## Phase N` completion of a ≥2-phase plan, inline, from its own loop — the operator never needs to run this skill for that. The shared contract both authors follow is `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/handoff/references/handoff-conventions.md`.

## Steps

1. **Resolve TARGET_DIR.**
   - If `args` starts with `/` or `~/`, set `TARGET_DIR` to that path (expand `~` via shell). Strip it off `args`; remainder is the focus hint.
   - Otherwise, `TARGET_DIR=$(pwd)`.
   - Normalize: ensure the directory exists. If it doesn't and the user passed it explicitly, **abort** with: `"cortex:handoff: TARGET_DIR <path> does not exist. Create it first or pick another path."`

2. **Guard: cortex:init project context** (only when TARGET_DIR was NOT passed explicitly via args).
   - Check via Bash: both `CLAUDE.md` AND `.claude/settings.json` exist directly inside `TARGET_DIR`.
   - If either is missing, **abort** with the exact message:
     `"cortex:handoff requires a cortex:init project context (missing: <list>). Run /cortex:init first, or pass an explicit absolute TARGET_DIR via args (e.g. /cortex:handoff ~/scratch)."`
   - This guard reproduces the chain-validation pattern used across cortex:plan / cortex:exec / cortex:end. When the user passes an explicit TARGET_DIR, they have opted out of the guard knowingly.

3. **Compute timestamp.** Run `date +%Y-%m-%d-%H%M` to get e.g. `2026-05-31-1734`. Store as `TS`.

4. **Scan TARGET_DIR for cortex artifacts** (anchor for survivability). Before composing, list which of these exist directly in `TARGET_DIR` and capture absolute paths:
   - `EXP.md`, `PLAN.md`, `EXEC.md`
   - `SESSION-SUMMARY.md`, `SOLUTION-OVERVIEW.md`
   - `.cortex-exec-receipt.json`, `.cortex-end-receipt.json`
   - `CLAUDE.md`, `.claude/settings.json`
   - `SETUP.md` *(if present — the one artifact that auto-reloads after `/compact` via `@SETUP.md` import)*
   - `handoff-*.md` *(existing checkpoints)*
   These references go into `## Generated Files` (see below). The point: after the user dispatches `/compact`, the conversation transcript is squashed — the handoff must point at durable artifacts on disk so the next turn can reconstruct context.

   > **No Team Mates section.** `cortex:exec` drives EXEC.md with plain **inline** subagents that return synchronously — there is no persistent team to resume across a `/compact`. Any subagent spawned this turn has already returned; its result lives in the artifacts and the `spawn_fallbacks` field of `.cortex-exec-receipt.json`, not in a resumable mate. Do not write a "Team Mates Status" section.

5. **Sweep setup facts into `SETUP.md`** (the reliable capture moment). `SETUP.md` is imported
   into `CLAUDE.md` via `@SETUP.md` — the one artifact **always** re-injected into context, on
   every turn. The handoff file is not auto-loaded that way — it is injected once, right after
   the compaction, by the compact-bridge hook (see "Post-compact resume" below). Both channels
   matter: setup facts belong here because they must hold for the whole cycle, not just the
   next turn.
   - From the live conversation, extract persistent setup/environment facts: `AWS_PROFILE`, AWS
     account/alias, region(s), kube context(s)/cluster(s)/namespace(s), terraform dir/workspace,
     other tool profiles, and "always use X / never touch Y" invariants established this cycle.
   - A long-lived local environment started this turn and not yet recorded gets its row under
     `SETUP.md`'s `## Local environment` section, so `/cortex:end` can tear it down at close. Row
     format: `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/end/SKILL.md` § "Local environment record".
   - If `SETUP.md` exists in `TARGET_DIR`, **merge** — preserve existing values, only set/update
     what surfaced this turn, append new invariants. If it does not exist, seed it from
     `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/setup-block.md.tmpl` (substitute `{{TS}}` with
     current date-time) and ensure **exactly one** bare `@SETUP.md` import line in `CLAUDE.md`
     (`grep -c '^@SETUP.md$' CLAUDE.md`; `0` → append a minimal Cycle-setup block; `>1` → remove
     extras keeping one).
   - Refresh the `Last updated:` line. **Redact secrets** — names/identifiers yes, credential
     values never.
   - If nothing setup-related surfaced this turn and `SETUP.md` already holds the facts, leave it
     untouched. Never edit the cortex:init marker block in `CLAUDE.md`.

6. **Compose the handoff content** by synthesizing the current conversation AND the artifact scan from step 4, with **the seven `##` sections defined in `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/handoff/references/handoff-conventions.md` § 2** — `Activity Understanding` · `Current Status` · `Key Decisions` · `Generated Files` · `Pending Items` · `Next Steps to Continue` · `Suggested Skills`, in that order, English-only regardless of conversation language. Read the reference before composing; it carries each section's definition and the grouping rule for `Generated Files` (group (a) this turn's files, group (b) the step-4 artifacts, `SETUP.md` always in (b) with the "auto-reloads via `@SETUP.md`" note). A focus hint from args is the primary anchor of `Next Steps to Continue`. When EXEC.md exists, the first paragraph of `Current Status` carries the derived count from `python3 ${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_exec_progress.py "$TARGET_DIR" --json` — never a hand-typed number.

   **These seven `##` headings are load-bearing.** `scripts/cortex_compact_bridge.py` parses three of them verbatim (`Current Status`, `Pending Items`, `Next Steps to Continue`) to build the post-compact brief. Renaming or reordering them silently degrades that brief to a bare pointer — the reference says so too.

7. **Write the file.** Use the `Write` tool to create `{TARGET_DIR}/handoff-{TS}.md`.

8. **Write the receipt.** Create `{TARGET_DIR}/.cortex-handoff-receipt.json` with the schema in `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/handoff/references/handoff-conventions.md` § 3 — the pointer the compact-bridge hook reads so it never has to guess which handoff is current. Overwrite it on every run (latest handoff wins). This skill writes `kind: "checkpoint"`, always `phase: null` — `phase-close` is `/cortex:exec`'s value. Apply the reference's `$(pwd)` copy rule when `TARGET_DIR` differs from a scaffolded `$(pwd)` (byte-identical copy; never "fix up" its paths).

9. **Print the closing block.** Output exactly this format (with the real absolute path substituted):

   ```
   Handoff saved at <ABSOLUTE_PATH>

   Next step — paste this in the prompt to compact the conversation with the same focus:

   /compact maintain understanding, status, decisions, files, pending — see handoff at <ABSOLUTE_PATH>

   No resume prompt needed afterwards — the compact-bridge hook re-injects this handoff automatically.
   ```

   Use forward-slashes in the path. If the user's conversation has been primarily in a language other than English, the `/compact` body line may be translated, but the surrounding instructions stay English to match the rest of the skill.

10. **End.** No further tool calls. Do not attempt to invoke `/compact` — see below.

## How `/compact` is handled

`/compact` is a **built-in Claude Code CLI command**, not a skill file in `~/.claude/skills/`. The CLI intercepts built-in slash commands at the input layer before they reach the model, so:

- The `Skill()` tool refuses built-ins by design (per the harness system prompt).
- No `Bash`, MCP, or other tool exposes the conversation-compaction operation as an API.
- Even between regular skills, `Skill(other)` mid-flow has no return semantics — control hands off and the calling skill ends.

The only viable mechanism is therefore to **print a ready-to-paste `/compact` command** for the user to dispatch manually. **Do not** try to "emit" the slash command in a message hoping the CLI picks it up — it will not. **Do not** try to fake it via Bash. Future edits to this skill must preserve this constraint.

## Post-compact resume

No manual step is needed after the compaction: the operator never has to ask the model to go read the handoff and the cycle artifacts. The plugin ships a `SessionStart` hook (matcher `compact`) in `hooks/hooks.json` that runs `scripts/cortex_compact_bridge.py`, which reads `.cortex-handoff-receipt.json` and injects a resume brief — cycle chain state plus this handoff's `Current Status`, `Pending Items` and `Next Steps to Continue` — as context, before the first prompt of the compacted session.

What this means for this skill:

- **Keep printing the `/compact` line.** It steers what the summarizer *retains*, which is independent of the hook.
- **Never re-introduce a "now ask the model to resume" instruction.** The hook covers it. If a future edit adds one back, the user is doing manual work the plugin already automates.
- **The receipt (step 8) is what makes it deterministic.** Without it the hook falls back to the newest `handoff-*.md` by mtime, which still works but is a heuristic.
- **A handoff older than the hook's freshness window is only pointed at, not inlined** — so a stale handoff never primes the next session with the wrong turn. That is the hook's decision, not this skill's; just don't fight it by, say, back-dating files.

## Constraints

- **The shared contract is the reference.** File names, the `-2/-3` idempotency suffix, the `.cortex-compact-log.json` prohibition, English-only body, secret redaction, never-duplicate-artifacts and the TARGET_DIR boundary all live in `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/handoff/references/handoff-conventions.md` §§ 1 and 5 — edit them there, not here, because `/cortex:exec` follows the same file.
- **Never invoke `/compact`.** Documented above; print the ready-to-paste line and stop.
- **Lead-only.** No `Agent()`, no `TeamCreate`. Subagents do not see the conversation transcript.
- **Three write targets only.** This skill writes the handoff file (step 7), its receipt `.cortex-handoff-receipt.json` (step 8, plus the `$(pwd)` copy when that is a distinct cycle), and may write/merge `SETUP.md` (step 5). The `SETUP.md` write may also append a one-time `@SETUP.md` import line to `CLAUDE.md` (backfill/self-heal). It never modifies `.claude/settings.json`, `PLAN.md`, `EXEC.md`, other receipts, or the cortex:init marker block.
- **Never write `.cortex-compact-log.json`.** That file is owned exclusively by the `PreCompact` branch of `scripts/cortex_compact_bridge.py` (a breadcrumb for automatic compactions). This skill only ever reads it, if at all.
