---
name: exec
description: Use when ready to execute a planned cycle — phrases like "cortex:exec", "execute the plan", "drive the EXEC.md", "run the plan". Reads EXEC.md from the target folder, spawns inline subagents per the Mates annotations, drives every unchecked task to its validation gate, ticks boxes in-place, closes every `## Phase N` of a ≥2-phase plan with a derived progress table + an inline handoff checkpoint (compact-safe), final review gate before finalizing (autonomous review loop through a ready PR for repo-mutating cycles, an optional advisor pass otherwise). Refuses if the EXP/PLAN/EXEC chain is incomplete. Resume-friendly via `.cortex-exec-receipt.json`.
---

# Skill: cortex:exec

Drives `EXEC.md` to completion. Honors the contract emitted by `cortex:plan`: inline subagents (plain `Agent(...)`, no `team_name`), `Mates:` annotations per phase (parallelizable | serial), `_(owner: <mate>)_` task tagging, validation gates that must pass before any tick.

When this skill finishes successfully, the cycle is ready for `cortex:end` to verify and ingest into ai-brain.

**Plugin root:** `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}`, written `<PLUGIN_ROOT>` in this skill's references.

## Coordination model — tag-driven, synchronous spawn batched by owner

Every task in EXEC.md carries one of two tags (set by cortex:plan):

- `_(inline)_` → **lead executes in main context.** No mate spawn. The lead runs the gate command itself, ticks the box. This is the default for any task whose gate is a single read-only tool call (`gh pr view`, `gh variable list`, `aws describe-*`, `kubectl get`, `terraform plan`, a regex grep).
- `_(owner: <mate>)_` → **batch-spawn the named subagent.** Group all `_(owner: <mate>)_` tasks of the same owner within the current phase into ONE subagent spawn. The subagent's prompt carries the batch. `<mate>` is a **planning label** carried in the spawn's `description:` — not a `name:` (see roles). The spawn pins `model:` from the tier annotated on the owner's `Mates:` entry — `(opus)` or `(sonnet)`, set by cortex:plan; an owner with no tier annotation (legacy EXEC.md) inherits the session model (omit `model:`).

**The `_(inline)_` tag is binding.** Even if EXEC.md also lists a mate in the phase's `Mates:` annotation, an `_(inline)_` task is NEVER given to a subagent. Tagging mistakes get surfaced — the executor does not "promote" inline tasks to spawned ones.

**Inline FLOOR — also binding, even when tagged `_(owner)_`.** Before spawning a subagent for an `_(owner: <mate>)_` task, the lead checks the gate. If the gate matches the "trivial read-only" pattern below, the task **runs inline regardless of its owner tag** — and the lead notes the over-eager tag in the receipt so cortex:plan can be corrected. This is the symmetric guard: planning judgment is not authoritative against the spawn cost.

Trivial read-only gate pattern (any match → inline floor applies):
- A single `gh ... view` / `gh ... list` call
- A single `aws ... describe-*` / `aws ... get-*` / `aws ... list-*` call
- A single `kubectl get ...` (no `apply`, `delete`, `patch`, etc.)
- A single `terraform plan` with no follow-up analysis
- A single `Grep` / `Read` against a small file set the lead can hold

Why this matters: a spawn costs far more than one read-only call, and an inline floor at exec time prevents that burn even when planning over-eagerly assigns ownership. The narrow criterion means legitimate subagent work (Terraform write, multi-file audit, schema design) is never demoted by accident.

**Untagged tasks:** apply the same inline heuristic: single read-only call → inline; otherwise spawn (or ask the user to retag).

**Batching rules (for `_(owner: ...)_` tasks):**
- One subagent spawn covers all of that owner's `_(owner: <mate>)_` tasks within the current phase, in EXEC.md order.
- Within a `parallelizable` phase: spawn one subagent per **owner** in parallel (multiple `Agent()` calls in one message).
- Within a `serial` phase: one subagent-batch at a time, in declaration order.

**Anti-pattern (what this prevents):** spawning a subagent to run a single `gh variable list` and report "available." The lead does that inline in seconds.

`Agent()` returns its result to the lead after the subagent's batch turn ends. The lead validates each task's gate (real tool call, quote output) and ticks EXEC.md per task — not per batch.

**Inline subagents only — no Team Agents.** Each spawn is a plain `Agent(description, subagent_type, prompt)` call that runs under the lead (like the `Explore` subagents in the planning skills) and returns its result to the lead after its turn (synchronously when Claude Code runs spawns inline). No `TeamCreate`, no `team_name`, no `SendMessage`/`TaskList`/`TeamDelete`. Inline subagents keep the whole run in one responsive context. Per-owner `model:` pins (from the `Mates:` tier annotations) change nothing here — the spawn is still plain, unnamed, and inline under the lead.

**Spawn contract — stall & failure protocol.** Spawns can fail at the infra layer (spawn "infra error / tmux pane issue") or hang with zero output, and Claude Code may run them inline (blocking) or in the background depending on version/settings. Every spawn follows this contract, leading with the safeguard that holds even when the lead is blocked:

- **Bound each batch in its prompt (primary safeguard).** End every batch prompt with: *"If you get stuck, can't finish a task, or a tool errors, return a per-task status report and STOP — don't keep retrying or spinning."* In inline mode the lead is blocked inside the `Agent()` call and **cannot** time out or `TaskStop` a running subagent — a self-terminating prompt, not a watchdog, bounds runtime.
- **Infra error on spawn**: if the spawn returns an *error* (not a result), retry **once**. If that also errors, the lead executes that owner's batch inline immediately — never queue more retries, never wait idle. Gates still run per task, exactly as if the subagent had done the work.
- **Watchdog (background mode only — belt-and-suspenders).** The prompt-bound above and the infra-error rule already cover runaway and errored spawns; the only gap is a subagent that hangs with *zero output forever* — unconfirmed the harness even allows it. If a phase comes back partial with one subagent still empty well past its timebox (~5 min read-only, ~10 min worker), wake yourself off the completion-notification path with a one-shot timer — `Bash("sleep 300; echo TIMEBOX", run_in_background: true)` — then on that wake `TaskList` → `TaskStop` the straggler → run its batch inline. Escalation only: don't arm it pre-emptively on every phase. No-op in inline mode. A bare timebox with no timer never fires — the lead idles until a *completion* notification a stuck subagent never sends.
- **Never hold a phase hostage to one stuck subagent** when siblings already returned — absorb the missing batch inline and move on.
- **Record every fallback** in the receipt's `spawn_fallbacks` field (schema below) so degraded parallelism stays visible across sessions instead of vanishing into a clean-looking receipt.

## Roles — lead + per-task subagents

- **Main context = lead.** Runs on the session model. Owns the drive-loop, gate validation, EXEC.md edits, advisor pass, receipt. **Single writer** to EXEC.md.
- **Subagents = workers** (the `<mate>` of each `_(owner: <mate>)_` tag — a planning label, not a spawn name). Spawned via plain `Agent(description, subagent_type, prompt)` with the `<mate>` label carried in `description:` and **no `name:`** — a named spawn becomes an in-process teammate that hangs in teams-enabled sessions and evades `TaskList`/`TaskStop`; unnamed keeps it a watchdog-visible background task that returns. **No `team_name`.** Each owner-spawn passes `model:` per the tier annotated on its `Mates:` entry — `(opus)` for reasoning-heavy batches (architecture, design, frontend/UX, security-sensitive logic), `(sonnet)` for well-scoped implementation, both assigned by cortex:plan; no annotation (legacy EXEC.md) → omit `model:` and inherit the session model. (The wrap-up review fan-out keeps its own per-reviewer pins — see `review-protocol.md`.) They run under the lead. Each subagent's prompt is **fully self-contained** — TARGET_DIR, the specific task ID + description, the PLAN.md context relevant to it, and explicit verify-don't-assume instructions. Subagents do NOT read the conversation; nothing is inherited.
- Subagent `subagent_type` defaults to `general-purpose`. Use `Explore` only when that subagent's task is read-only.

## Input

Free-text `args`:
- Optional `folder:<name>` prefix (relative to CWD; reject `..` or absolute paths).
- Remainder ignored (used as ad-hoc note only).

Examples:
- (no args) → execute the cycle in CWD.
- `folder:tuesday-deploys` → execute the cycle in that subfolder.

## Steps

1. **Parse args.** Strip `folder:<name>` (reject `..`, abs paths). `TARGET_DIR = ${CWD}/${folder}` or `${CWD}`.

2. **Verify the chain.** `EXP.md`, `PLAN.md`, `EXEC.md` must all exist at `TARGET_DIR`. If any missing, abort: *"cortex:exec requires the explore → plan → exec chain. Missing: <list>. Run /cortex:plan first."*

3. **Receipt check (idempotency).** If `${TARGET_DIR}/.cortex-exec-receipt.json` exists:
   - `status: "complete"` → tell the user the cycle was already executed; ask whether to re-run from scratch (rare) or abort.
   - `status: "partial" | "aborted"` → enter **resume mode**: re-read EXEC.md, skip already-`[x]` tasks, pick up where the prior run stopped. A `partial` receipt alone is not a block (the phase close writes it).

3b. **Access preflight (before Phase 1, and again on every resume).** When PLAN.md has an `## Access` section, run every probe it lists, in one batch.
   - **What a probe may be.** An identity or status call, judged by its exit status alone: stdout and stderr go to `/dev/null`. The lead refuses, and never runs, a probe that prints a credential (`aws ecr get-login-password`, `aws configure export-credentials`, `gh auth token`, `kubectl config view --raw`), takes a lock or writes state (`terraform plan`), or opens a long-lived process (`port-forward`, `ssh -fN`). Such an item goes to the question below unprobed.
   - **Failures.** Every failing item (an expired SSO session, a missing tunnel, a CLI not logged in) goes into **one** `AskUserQuestion`, now and never mid-phase. It has header `Gate` and the fixed labels `Retry` (after the operator logs in or opens the tunnel; re-runs the failing probes) and `Abort cycle`. The question names each item and its fixed reason (`<probe> exited <code>`), never raw output. It is a hard blocker: only the operator can sign in.
   - All probes pass → continue silently.

4. **Resume state (no team to recover).** With inline subagents there is no persistent team to inspect or wipe — a returned subagent is already gone. Resume is driven entirely by the receipt + checkbox state: if the receipt is `partial`/`aborted`, re-read EXEC.md and skip every task already marked `[x]`, picking up at the first open `- [ ]`. **Start clock:** with no receipt yet (a first run) or on a re-run from scratch (step 3), run `date -u +%Y-%m-%dT%H:%M:%SZ` now and keep its output as `started_at` — the moment execution actually began; a resume keeps the receipt's `started_at` untouched. On resume, a `pause` moves into `pauses[]` with `resolution {"decided_by": "resume", "decision": "re-run gate", "at": "<iso>"}` and `pause` goes back to `null` — the paused task's gate re-runs, no approval needed. `checkpoints` is informational — never re-write a checkpoint for a phase already listed there.

5. **Parse EXEC.md** to extract:
   - Phase order: `Cross-cutting > Setup` (front, if present), every `## Phase N — <name>` heading in document order, plus `Cross-cutting > Wrap up` (back).
   - Per phase: the `> **Mates:** ...` annotation (subagent role names, each with its optional model tier `(opus)`/`(sonnet)`, + `parallelizable` vs `serial`), and the list of `- [ ] <task>` lines with their `_(owner: <mate>)_` tag and `**Gate:** <gate>` follow-up line.
   - Total distinct subagent role names referenced across all phases.

6. **Drive each phase in order.** For each `## Phase N`:
   a. **Re-read EXEC.md fresh** (catches mid-run user edits). Lock the phase to this snapshot.
   b. Filter to `- [ ]` tasks within this phase. Group by `owner`.
   c. **Split tasks by tag** (see "Coordination model" above):
      - `_(inline)_` tasks → the lead executes them directly, in EXEC.md order. No spawn. The lead runs the gate command (which IS the task) and ticks. Multiple inline tasks can run with the lead's own parallel tool calls if independent.
      - `_(owner: <mate>)_` tasks → group by owner, then batch-spawn one subagent per owner with all its phase tasks in the prompt, passing `model:` from the owner's `Mates:` tier (omit when unannotated — session model).
      - Untagged tasks → apply the inline heuristic (single read-only tool call = inline; otherwise spawn).
   d. **Within a `parallelizable` phase**: issue all the owner-batch spawns (and any inline checks) in a single message. Subagents run concurrently; lead observes returns.
   e. **Within a `serial` phase**: one owner batch at a time, in EXEC.md order.
   f. **Each subagent batch prompt is self-contained** and MUST include:
      - `TARGET_DIR` path (and the pinned worktree path when the batch mutates a repo — see the worktree-first constraint)
      - The **list** of task descriptions from EXEC.md this batch covers (ordered)
      - The validation gate for each task (so the subagent can self-check before reporting done)
      - Relevant excerpts from PLAN.md (the lead extracts these — the subagent cannot read the conversation)
      - **For test/build/lint-gated tasks: the whole iterate-until-green loop.** Instruct the owner to run the gate, fix, and re-run until it passes — returning to the lead on every failure defeats the context offload the `_(owner)_` tag exists for. The stuck-stop instruction below bounds the loop.
      - **Test-run discipline:**
        - Iterate with the narrowest filter that covers the change: one file, one test class.
        - Run each command once with `tee` to a file under the scratchpad or `/tmp`, and grep that file, instead of re-running the suite to see another part of its output.
        - Run the broad suite once, at the end, before handing back, and report its summary line. It ran over uncommitted edits, so it is the subagent's own check: the lead records it in `suite_runs[]` with `clean: false`, and it is never reused as a pass.
      - **Code style:** match the surrounding code's comment density, naming and idiom. Write no docblock narratives, no defensive branches for states that cannot occur, and nothing the acceptance criteria or a confirmed finding does not ask for (over-engineering, as `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/exec/references/advisor-agenda.md` defines it).
      - Explicit instruction: *"Verify external state via real tools (gh, aws, kubectl, git, etc.) and quote output. Never assume from prose. Work through the tasks in the listed order. For each task, report its outcome separately. If you get stuck, can't finish a task, or a tool errors, return a per-task status report and STOP — don't keep retrying or spinning."*
   g. **After each subagent returns** (or each inline gate runs), the lead, **per task**:
      - Reads the subagent's per-task outcome (or the inline tool output).
      - **Runs the validation gate itself** if the subagent's evidence is insufficient — gate is source of truth, the subagent's word is not enough. If gate is a tool command (`gh pr view`, `aws describe-*`, `terraform plan`), execute it and quote output. If gate is `_(manual review)_`, surface the report and `AskUserQuestion` the user (header `Gate`).
      - On **gate pass** → tick EXEC.md (see "Ticking mechanism" below).
      - On **gate fail** → pause the phase, `AskUserQuestion`: `Retry` / `Skip with caveat` / `Abort cycle`. No silent retries. `Abort cycle` (`status: "aborted"`) or no answer (`status: "partial"`) writes the receipt with its `pause` block (step 8) and stops.
      - **Evidence contradicts the plan** (a task's real state, a subagent report or a gate output shows a PLAN.md premise is wrong) → classify per § "When evidence contradicts the plan" before anything else, and act on that row. An optional addition's entry is `triage: optional` with outcome `follow-up:<receipt note>`, and an effect a user can see goes under `## Known effects` in the PR body. Never adapt a material divergence silently, and never turn a technical gate failure into a divergence. A material divergence left unanswered writes the receipt with `status: "partial"` and stops; the next run reaches the same evidence and asks again.
      - **When the task started a long-lived local environment** (compose stack, dev server, tunnel), the lead records it in `SETUP.md`'s `## Local environment` section (row format in `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/end/SKILL.md` § "Local environment record") so `/cortex:end`'s cleanup can tear it down at close.
   h. A phase is complete when all its `- [ ]` tasks are now `- [x]` (or were skipped with caveat). **Then run the phase close** (§ "Phase close" below) before starting the next phase — it is loop machinery, not a task.

7. **`Cross-cutting > Wrap up` tasks** are driven by the lead, not by subagents:
   - **"Confirm docs flagged for update"** → `AskUserQuestion` with PLAN.md's "Docs flagged for update" entries as options. Record the user's choices in the receipt. Do NOT auto-edit docs.
   - **"Ship & autonomous review"** (emitted by cortex:plan when the cycle mutates a repo) → the lead drives it **inline via references** — read `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit/references/commit-conventions.md`, `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit-push-pr/references/pr-conventions.md`, and `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/review/references/review-protocol.md`, then execute them verbatim: commit → push → `gh pr create --draft` → reviewer fan-out ‖ test-run probe (background) → debate → fix rounds (hard cap 2, then the protocol's advisor round; suite re-run per batch) → delivery comment → `gh pr ready` (or stay-draft on non-convergence, required checks, or an approved scope that ends at a draft PR). No `Skill(...)` chaining — the references ARE the contract (receipt location + schema included in the protocol). Tick when the protocol reaches one of its four terminal states: `ready` · `draft + "needs human"` comment · `draft + "converged (ready blocked by required checks)"` comment · `draft + "converged (kept draft by approved scope)"` comment — recorded as the receipt's `terminal` (never improvised prose). The tick carries the PR's URL (§ "Ticking mechanism").
     - **A small plan is re-checked before shipping.** Before the ship step, the lead checks PLAN.md's `> Profile: small` criteria against the real diff: `git diff --numstat <base>...HEAD`, summing every file outside the test class, and a scan for the excluded areas. A criterion broken → record it in `discovered_work` (`triage: adjustment`, `adjusted: profile standard — <criterion>`); the review then runs the standard matrix.
     - **The review ends `draft-needs-human` on blocking cap items.** The protocol's cap classification leaves only three kinds blocking: high, floor-area, and a medium or high regression this loop introduced. The rest, a low regression outside the floor areas included, are non-blocking and listed in the delivery comment. Before the loop ends, the protocol's advisor round (§ "Advisor round") may grant one more round. Not granted, the lead asks the protocol's fixed-shape `Review round` question (§ "Review round") before any pause handoff, and again after each granted round that still ends with blocking items. On `Grant one more round`, it runs one more fix round scoped to those items. On `Accept residual risk`, it records them `deferred:risk` and converges. On `Stop: needs human`, the PR stays draft.
     - **A second PR in the same cycle** (opened after the first, e.g. an IaC reconciliation the cycle needs) is shipped and reviewed with the same protocol run inline, standalone-draft against that PR. Its receipt goes at `<cycle>/.cortex-review-receipts/<owner>__<repo>__<n>.json` (protocol § "Receipt") and records that PR's own `worktree`. It is never reviewed lead-only, and it never overwrites the canonical receipt or uses an ad-hoc sidecar. Cite the PR in EXEC.md (its URL), so `/cortex:end`'s merge gate covers it.
   - **"Final advisor review on the implementation"** (emitted only for cycles that touch no repo) → call `advisor()` when the tool is available. If blocking findings, surface and `AskUserQuestion`: `Address findings now` / `Defer to caveat in receipt` / `Abort`. Tick only after handled. When it is unavailable, never block or ask: tick the task with the note `(advisor unavailable)` and add `advisor unavailable: not reviewed` to the receipt's `caveats` (step 8; PLAN.md stays read-only).

8. **Write receipt** at `${TARGET_DIR}/.cortex-exec-receipt.json`:
   ```json
   {
     "started_at": "<iso>",
     "completed_at": "<iso or null>",
     "phases_completed": ["Phase 1 — <name>", "..."],
     "checkpoints": [{"phase": "Phase 1 — <name>", "handoff": "<abs path of handoff-<TS>.md>", "written_at": "<iso>"}],
     "pause": null,
     "pauses": [],
     "tasks_done": <int>,
     "tasks_skipped": [{"task": "...", "phase": "...", "reason": "..."}],
     "docs_decisions": [{"doc": "...", "decision": "update | skip"}],
     "advisor_pass": true,
     "caveats": ["<one line each: a deferred advisor finding, or advisor unavailable: not reviewed>"],
     "review_receipt": "<path to .cortex-review-receipt.json, or null when the advisor task ran instead>",
     "suite_runs": [{"head": "<commit sha the full suite ran at>", "runner": "<the exact command>", "result": "passed | failed:<n> | timeout | env-blocked", "clean": "<true only when `git status --porcelain` (untracked files included) was empty at the run>", "at": "<iso>", "by": "exec | review"}],
     "worktree": "<absolute path of the product repo worktree this run wrote to (the pinned worktree-first path), or null when no repo was mutated>",
     "spawn_fallbacks": [{"agent": "<name>", "reason": "spawn-infra-error | watchdog-timeout", "resolution": "inline"}],
     "discovered_work": [{"description": "...", "evidence": "...", "triage": "adjustment | optional", "outcome": "adjusted:<what changed> | follow-up:<receipt note>"}],
     "status": "complete | partial | aborted"
   }
   ```
   **`suite_runs[]` is the cycle's one record of full-suite runs.** It holds one entry per run of the repo's whole suite: a phase gate, a subagent's final broad run as it reported it, the review's preflight, or a fix batch. **One full run per head:** before running the whole suite, look at the **latest** entry for the current `HEAD` and the exact runner command. When it is `passed` and `clean: true`, reuse it and do not run again. A failed, dirty or filtered run is never reused as a pass.

   **Every timestamp here is a clock reading, never an estimate:** `started_at` is step 4's start clock; `completed_at`, each `written_at` and any `at` are the output of `date -u +%Y-%m-%dT%H:%M:%SZ` run at the moment that field is written. Never fill one from memory, the conversation or a guess — a start no clock reading or session event establishes stays `null` rather than invented.

   `spawn_fallbacks` is `[]` when every spawn behaved — include the empty array anyway, so its absence in old receipts is distinguishable from "no fallbacks". `checkpoints` lists every phase-close handoff this run wrote (§ "Phase close"); `[]` when the ≥2-phase guard skipped them, so absence (old receipts) stays distinguishable from "skipped". `pause` is `null` except when the run stopped on a failing gate (step 6g): `{"kind": "technical", "task": "...", "gate": "...", "at": "<iso>"}` (moved into `pauses[]` on resume). `pauses[]` is the history, each entry carrying its `resolution`. `advisor_pass` is `true` when the review loop converged OR `advisor()` ran clean — whichever gate this cycle used — and `false` when the advisor was unavailable; advisor output never counts as review evidence. `caveats` is `[]` when there is none. `review_receipt` points at the loop's own receipt when the ship-and-review path ran. `worktree` is the absolute path of the repo worktree the run mutated (the first, when several were).

9. **Print final summary:**
    ```
    ✓ Cycle executed at <TARGET_DIR>
    • Subagents: <N> spawned (inline · <K> opus, <L> sonnet[, <M> session-model])
    • Phases completed: <list>
    • Checkpoints: <K> phase-close handoffs (or "skipped — single-phase plan")
    • Tasks: <done> done, <skipped> skipped with caveat
    • Review gate: <loop converged (PR ready) | loop needs-human (PR draft) | loop converged-blocked-by-checks (PR draft) | advisor pass | advisor findings addressed | advisor unavailable>
    • Review receipt: <path to .cortex-review-receipt.json, or n/a for advisor-gated cycles>
    → Run /cortex:end to verify and ingest into ai-brain.
    ```

## Ticking mechanism (in-place EXEC.md edits)

- **Only the lead edits EXEC.md.** Subagents NEVER write to it — parallel subagents racing on a single file would corrupt it. They report up; the lead is the single writer.
- Use the `Edit` tool with a **unique full-line match including the task's tag** — either `_(inline)_` or `_(owner: <mate>)_`, whichever is present. Examples:
  ```
  old_string: "- [ ] Confirm API_BASE_URL key available _(inline)_"
  new_string: "- [x] Confirm API_BASE_URL key available _(inline)_"

  old_string: "- [ ] Configure webhook(s) on GitHub ... _(owner: gha-config)_"
  new_string: "- [x] Configure webhook(s) on GitHub ... _(owner: gha-config)_"
  ```
- If the title + tag still isn't unique, include the `**Gate:** ...` line below it OR the `> **Mates:** ...` line above it to disambiguate.
- **The Ship tick carries the PR URL.** Ticking the `Ship & autonomous review` line appends ` → PR <url>` at the end of the line. The URL is the PR's `gh pr view <num> --json url` value. It must be non-empty and end with `/<owner>/<repo>/pull/<n>`: the review receipt's `repo` and `pr`, with a `/` before the owner, compared case-insensitively, whatever the host. A URL that cannot be read or does not match keeps the tick pending, with a receipt note (`discovered_work`, `triage: adjustment`, outcome `adjusted:Ship tick pending — <why>`). A line that already holds `→ PR <that url>` is ticked without appending again, so a re-run or a resume never writes it twice; one that holds another URL has it replaced:
  ```
  old_string: "- [ ] Ship & autonomous review: commit → push → draft PR → review loop → ready _(inline)_"
  new_string: "- [x] Ship & autonomous review: commit → push → draft PR → review loop → ready _(inline)_ → PR https://github.com/<owner>/<repo>/pull/<n>"
  ```
- Always Read EXEC.md immediately before the Edit so the match is against current content (the user may have made edits between phases).
- If the Edit fails (no match), abort the task with a clear error — never fuzzy-retry.

## Phase close

Runs at every `## Phase N` completion (step 6h) **when EXEC.md holds ≥2 `## Phase N` headings** (`python3 ${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_exec_progress.py "$TARGET_DIR" --json` → `phase_count >= 2`). A single-phase plan skips it; `Cross-cutting > Setup` / `Wrap up` completions never trigger it. The checkpoint is what makes a mid-run auto-compact safe (the compact bridge re-injects it), and the table is what gives the operator visibility; both belong to the loop, not to the plan.

1. **Re-read EXEC.md** (the tick state IS the truth) and confirm every task in the phase is `[x]` or skipped.
2. **Print the progress block** — run `python3 ${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_exec_progress.py "$TARGET_DIR" --table --ledes '<json>'` and print its output **verbatim**, under one headline in the conversation's language: `**<done> of <total>.** <one sentence: what just closed, what starts now>`. `--ledes` is `{"<short label>": "<≤60 chars>"}` for the closed phases and the phase about to start only (e.g. `{"Phase 1": "six modules, every gate re-run by the lead", "Phase 2": "← first real apply"}`) — that column is the lead's knowledge; the counts are the script's. **Never type a count by hand.** The same block (ledes refreshed) is the natural shape for any mid-phase progress report.
3. **Author the checkpoint inline** — `handoff-<TS>.md` per `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/handoff/references/handoff-conventions.md` (the seven headings; § 4 there is this author's contract: `## Current Status` = the headline sentence then the table, `## Generated Files` = the phase's touched files + commit SHA, `## Next Steps to Continue` opens with `Continue /cortex:exec at <next phase>`). Written by the lead from the live conversation — never `Skill(cortex:handoff)` (no return semantics; the calling loop dies) and never a subagent (no transcript).
4. **Write the handoff receipt** `.cortex-handoff-receipt.json` with `kind: "phase-close"` and `phase: "<verbatim heading>"` (+ the `$(pwd)` copy rule from the reference).
5. **Refresh the exec receipt** (step 8 schema) with `status: "partial"`, the phase appended to `phases_completed`, and the checkpoint appended to `checkpoints`; `started_at` is preserved from the existing receipt — or, on the first write, is step 4's start clock (never a new reading) — and `tasks_done` is the script's `done` count. `pause` stays `null`.
6. **Continue into the next phase.** No pause, no `/compact` line — the operator compacts when they choose; the checkpoint only guarantees that an automatic compaction loses nothing.

## When evidence contradicts the plan

| What was found | What to do |
|---|---|
| Technical adjustment: objective, scope and acceptance criteria still hold, the gate verifies the same outcome | Record it in the exec receipt's `discovered_work` (`triage: adjustment`, evidence, what changed) and continue |
| Optional or out-of-scope addition | Note it in the receipt and the PR. Never add it to EXEC.md |
| Material divergence: objective, scope or acceptance criteria no longer hold | Ask the operator once with `AskUserQuestion` and stop until answered. Silence never authorizes |

## Constraints

- **English only in any text written to EXEC.md or the receipt.** Ticking flips `[ ]` to `[x]` — the task text itself stays as it was (already English from cortex:plan). If a task is amended (e.g., skip-with-caveat comment), the caveat is written in English. Subagent prompts must instruct reports in English.
- **Honor the `_(inline)_` tag absolutely.** An `_(inline)_` task is NEVER spawned, even if a phase's `Mates:` annotation lists a mate. The tag is the authoritative signal from cortex:plan that this task is too small to justify a spawn. Promoting inline tasks to spawned ones is the over-spawn anti-pattern. Symmetric receipt note: if an `_(inline)_` task turns out to involve multi-file authoring or heavy analysis (mis-tagged under cortex:plan's owner-first discipline), still honor the tag — but record the over-eager inline tag in the receipt so cortex:plan can be corrected, mirroring the inline-floor note in the other direction.
- **Plain inline subagents only.** No `TeamCreate`, no `team_name` — every **execution mate** is a plain unnamed `Agent(description, subagent_type, prompt)` call that runs inline under the lead, with `model:` pinned per the owner's `Mates:` tier annotation (`opus` | `sonnet`; unannotated legacy owners inherit the session model). **Honor the plan's tier** — never silently up- or down-tier an owner at exec time; if a tier looks wrong (e.g. `sonnet` on a batch that turned out to be gnarly architecture work), keep the pin and record the mis-tier in the receipt so cortex:plan can be corrected — mirroring the inline-floor note. (The wrap-up review fan-out is a separate contract: its reviewers are pinned per row in `review-protocol.md`.) Team Agents are forbidden here.
- **Worktree-first for any repo mutation — verified, and self-healed inline if missing.** Before any subagent writes to a repo, the lead verifies (`git -C <repo> rev-parse --show-toplevel` then `git -C <repo> worktree list`) that a `.worktree/<prefix>-<slug>/` already exists for that repo and pins its absolute path into every subagent's prompt as the working directory. Producing the worktree is normally `/cortex:new-worktree`'s job (cortex:plan should have emitted that task in `Cross-cutting > Setup`); if it's missing AT EXEC TIME, the lead inlines the worktree-chain (Bash, below) BEFORE the first repo-mutating spawn — do NOT invoke `cortex:new-worktree` via the `Skill` tool (the no-return-semantics trap kills the run mid-flow). The inline chain reuses the slug derived from `PLAN.md`'s cycle title (or `AskUserQuestion` if genuinely ambiguous) and a prefix inferred from the phase intent (`feat` for new behavior, `fix` for bug, `chore` for housekeeping). Rationale: writing to `main` in the source repo bypasses review, blocks rollback, and corrupts the cycle's audit trail.

  **Inline worktree-chain** — only runs when the verify step found no matching worktree:

   ```bash
   cd "${REPO_DIR}" && \
     git fetch --prune origin && \
     git checkout main && \
     git pull --ff-only origin main && \
     mkdir -p .worktree && \
     ( grep -qxE '\.worktree/?' .gitignore 2>/dev/null || printf '.worktree/\n' >> .gitignore ) && \
     git worktree add -b "${BRANCH_PREFIX}/${FEATURE_SLUG}" ".worktree/${BRANCH_PREFIX}-${FEATURE_SLUG}" main
   ```

   Hard failures abort the phase with the raw git output: `pull --ff-only` divergence (user reconciles `main` manually), `worktree add` collision (ask for a new slug). After success, the lead uses the new worktree path as the working directory for every repo-mutating spawn this phase.
- **Commits are lead-only and follow `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit/references/commit-conventions.md` inline.** Subagents NEVER run `git commit` — parallel subagents against the same worktree's index will race. When a phase produces committable changes, the lead reads the conventions reference and serializes the commit itself (after the batch returns). Do NOT route through `Skill(cortex:commit ...)` mid-run — no-return-semantics trap. If the lead can't draft a confident message, pause the loop and ask the user to invoke `/cortex:commit` standalone. Subagent prompts must explicitly say "do not commit — return the changed file list and a short rationale; the lead will commit."
- **Pushing and opening PRs is wrap-up work, driven by the lead — never by subagents, never mid-phase.** Subagents never push, never call `gh pr create`. When EXEC.md carries the **"Ship & autonomous review"** wrap-up task (cortex:plan emits it for every repo-mutating cycle), the lead ships **inline via references** — `pr-conventions.md` for the draft PR, `review-protocol.md` for the loop through `gh pr ready` (see step 7). No `Skill(...)` chaining; the references are the contract. Only when a PR is genuinely needed mid-cycle (rare) or the EXEC.md predates the ship task and the user prefers manual control does the lead pause and ask the user to invoke `/cortex:commit-push-pr` standalone.
- **Sync per-task spawn only.** Each subagent is spawned, does its batch, returns; the lead observes the return and proceeds. No async `SendMessage`-as-new-turn flows and no shutdown protocol — a returned subagent is already gone.
- **Only the lead edits EXEC.md and the receipt.** Subagents report — they don't write.
- **Verify, never trust.** Gates always run the real tool and quote output. A subagent saying "I did it" is not a gate pass.
- **No silent scope additions.** If a subagent discovers new work, it reports back; the lead classifies it per § "When evidence contradicts the plan". New tasks NEVER appear in EXEC.md without user consent — that path is `/cortex:plan` re-run. **Out-of-scope findings from the review loop are NOT scope additions** — they never enter EXEC.md and never widen the PR; they live only in the delivery comment.
- **Subagent prompts must be self-contained.** Subagents cannot see the conversation or any prior tool output; everything they need (TARGET_DIR, the pinned worktree path, task details, PLAN.md excerpts, verify-don't-assume rule) MUST be in their prompt.
- **No Plan Mode.** Execution does not bracket with `EnterPlanMode` / `ExitPlanMode` — those are for plan-producing skills.
- **The final gate before finalizing.** Repo-mutating cycles: the autonomous review loop (`review-protocol.md`) IS the gate and is non-optional — `advisor()` joins its fan-out as a voice when reachable and is skipped silently otherwise. Non-repo cycles: `advisor()` is the gate when the tool is available; when it is not, tick the task with `(advisor unavailable)`, add the `caveats` note and continue, never blocking or asking. Advisor output never counts as review evidence.
- `TARGET_DIR` must be inside or equal to CWD.
- Produces no new artifacts other than `.cortex-exec-receipt.json` and in-place EXEC.md ticks. PLAN.md and EXP.md are read-only.

## Out of scope

- **Replanning.** If the plan is wrong, exit and run `/cortex:plan` again. cortex:exec does not modify PLAN.md or add phases to EXEC.md.
- **Editing docs flagged for update.** Capture the user's intent (update or skip) in the receipt; the actual doc edits are user work or future-skill work.
- **Closing PRs / ingesting to ai-brain.** That's `/cortex:end`.
- **Plan Mode bracketing.** Execution is not a plan; do not enter Plan Mode.
