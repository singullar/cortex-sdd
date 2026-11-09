---
name: end
description: Use when closing a Spec-Driven Development work cycle — phrases like "cortex:end", "close the cycle", "we're done with this", "wrap the cycle". Verifies every PR cited in PLAN.md is MERGED AND every task in EXEC.md is checked off AND the cycle carries review evidence (a completed `.cortex-review-receipt.json` from the cortex:review loop — advisor output alone does not count) whenever code/IaC changed — skippable only when review was not needed (no repo mutation) or by the operator's explicit AskUserQuestion waiver. If yes, distills the session into structured knowledge and saves it to `SESSION-SUMMARY.md`, then — when the close is clean (every gate green) — chains straight into `/cortex:ingest` as its final act so the knowledge lands in ai-brain without a second command; the operator can still pick `Close now, ingest later` (take the long ingestor block in another session) or `Let me edit first`. Refuses while work is incomplete — surfaces what's still open instead; an open, unmerged PR gets a structured delivery handoff (executed · delivered · merged · closed, PR link, remaining action + owner, resume path) because a PR-only delivery is a checkpoint, never a closure.
---

# Skill: cortex:end

Closes a Spec-Driven Development work cycle: verify the work landed, distill the lasting knowledge into `SESSION-SUMMARY.md`. `/cortex:ingest` is the dedicated step that pushes the distillation into ai-brain — a separate skill so the long ingestor call is isolated from the gate-and-distill flow, and reached automatically: a clean close tail-chains into it as this skill's final act (step 10).

This skill is the **only** gate ai-brain ingestion passes through. If gates are red, no `SESSION-SUMMARY.md` is produced, so `/cortex:ingest` has nothing to feed the vault — partial/wrong knowledge can't pollute it.

## Roles — lead + subagents (no Team Agents)

Like cortex:plan, this is one-shot verification + a single distillation pass. No `TeamCreate`, no `team_name`. Lead on opus; subagents on sonnet for parallel verification and distillation, spawned **unnamed** (role in `description:`, never `name:` — a named spawn becomes an in-process teammate that hangs the lead in teams-enabled sessions and evades `TaskList`/`TaskStop`). Ingestion is out of scope — `/cortex:ingest` owns the `ingestor` agent call. This skill's own work stops at `SESSION-SUMMARY.md` + the receipt; a clean close then hands the session to `/cortex:ingest` through the tail-chain (step 10), never by spawning the ingestor here.

## Input

Free-text `args`. Optional `folder:<name>` prefix (relative to CWD); rest is ignored.

Examples:
- (no args) → close the cycle in CWD.
- `folder:tuesday-deploys` → close that subfolder's cycle.

## Steps

1. **Parse args.** Strip `folder:<name>` (reject `..`, abs paths). `TARGET_DIR = ${CWD}/${folder}` or `${CWD}`.

2. **Required artifacts.** Verify `EXP.md`, `PLAN.md`, `EXEC.md` exist in `TARGET_DIR`. If any missing, abort: *"cortex:end requires the explore → plan → exec chain. Missing: <list>. If this isn't a complete cycle, you don't need cortex:end."*

3. **Receipt check (idempotency).** If `${TARGET_DIR}/.cortex-end-receipt.json` exists with `ingested: true`, the cycle was already fully closed and ingested by `/cortex:ingest`. Abort with: *"Cycle was already closed at <closed_at> and ingested at <ingested_at>. To redo from scratch, manually delete both `.cortex-end-receipt.json` and `SESSION-SUMMARY.md` and re-run /cortex:end. To re-ingest only (e.g. after editing SESSION-SUMMARY.md), run `/cortex:ingest` directly."* Otherwise, if the receipt exists with `ingested: false`, do not abort — proceed (the prior run gated successfully but ingestion was deferred; this run will refresh the distillation).

4. **Verification phase — gates the whole skill.** Spawn parallel subagents (single message, multiple `Agent(...)` calls). No `team_name`. Each returns a compact status report.

   - `Agent(description: "pr-scout", subagent_type: "general-purpose", model: "sonnet", prompt: "Parse PLAN.md and EXEC.md at <TARGET_DIR> for **every** GitHub PR reference, matching ALL of these forms: (a) '<org>/<repo>#<num>'; (b) 'gh pr view <num> -R <owner/repo>'; (c) full URL 'https://github.com/<owner>/<repo>/pull/<num>'. Deduplicate by (owner/repo, num). For each unique PR, run 'gh pr view <num> -R <owner/repo> --json number,state,mergedAt,closedAt,headRefName,headRefOid,isDraft,url' and quote the JSON (headRefName/headRefOid are consumed later by step 9's worktree cleanup, isDraft/url by step 5's delivery handoff — always include them). Report PASS only if state==MERGED. Report FAIL with the actual state otherwise. A ref whose gh call fails with 'Could not resolve to a PullRequest' is an issue cited in the plain form, not a PR: report it as NOT-A-PR with the quoted error — never PASS, never FAIL, and leave it out of the count. Any other gh failure (auth, network, rate limit) is FAIL (unverified) with the quoted error. Do not assume — always run the gh command. FIRST line of your return MUST be 'Detected PRs: <count> — <list of owner/repo#num>' (NOT-A-PR refs excluded), then 'Issues cited (not PRs): <list or none>'. Then one block per ref. If count is zero, say so loudly — do not just return 'no failures'.")`
   - `Agent(description: "exec-scout", subagent_type: "Explore", model: "sonnet", prompt: "Read EXEC.md at <TARGET_DIR>. Count '- [ ]' (open) and '- [x]' (done). List EVERY open task verbatim with its phase. Return: open_count, done_count, list_of_open.")`
   - `Agent(description: "state-scout", subagent_type: "general-purpose", model: "sonnet", prompt: "Read PLAN.md at <TARGET_DIR>. Identify any cited external state beyond PRs — cloud resources, deployments, ticket statuses, branch states. For each, run the appropriate read-only tool (aws describe-*, kubectl get, git ls-remote, etc.) and quote the output. Report DRIFT if reality contradicts what PLAN.md claimed; PASS otherwise. Do not block on items you cannot verify — list them as 'unverifiable, manual check needed'.")`

   **Spawn contract — stall & failure protocol** (applies to these scouts and to the step-6b distiller):
   - **Infra error on spawn**: retry **once**; on a second failure the lead runs that scout's checks inline immediately (the gh/read-only commands are all runnable from the main context) — never queue more retries, never wait idle.
   - **Watchdog**: scouts ~5 min, distiller ~10 min. Timebox expired with no result → `TaskStop`, do the work inline.
   - **Don't hold the gate hostage to one stuck scout** when the others already returned — absorb the missing checks inline.
   - **Record every fallback** in the receipt's `spawn_fallbacks` field (see step 8).

4b. **Review-evidence check (lead-inline, no spawn — it's a local file read).** The EXEC.md checkbox for the review task is not proof by itself (it can be ticked by hand); the receipt is. Policy: **review may be skipped only when it was not needed (the cycle mutated no code/IaC/repo) or when the human operator explicitly waives it** — there is no softer lane. **"Review" means the cortex:review autonomous loop (reviewer fan-out → debate → fix → delivery), evidenced by its own `.cortex-review-receipt.json` — the advisor is a different mechanism and advisor output alone is NOT review evidence.** Classify, in order:

   - **Review not needed** — pr-scout confirmed 0 PRs (including the user-confirmed 0-PR lane from step 5) AND EXEC.md/PLAN.md show no repo-mutating or IaC-mutating work (no worktree tasks, no `terraform apply`/`kubectl apply`-style mutations — read-only ops don't count). → PASS as `n/a (no code/IaC changes)`, no question asked.
   - **Review loop ran** — locate the loop receipt: `review_receipt` path in `${TARGET_DIR}/.cortex-exec-receipt.json` (non-null), else `${TARGET_DIR}/.cortex-review-receipt.json` directly (covers standalone `/cortex:review` runs after a manual ship). **Every PR the cycle cites** needs its own evidence: a second PR of the cycle is covered only by its receipt in `${TARGET_DIR}/.cortex-review-receipts/` (`<owner>__<repo>__<n>.json`, review-protocol § "Receipt"), read the same way; a cited PR with neither receipt falls to the waiver lane, named. Read it: `status: "complete"` → PASS as `passed`. `status: "partial" | "aborted"` → the loop did NOT finish — not evidence, fall through to the waiver lane naming that status. Cited path missing on disk → print `⚠ review receipt cited but not found at <path>` and fall through (an unverifiable claim is not evidence).
   - **Anything else** — code/IaC changed and the review loop has no completed receipt (no receipt at all, advisor-only cycle, loop partial/aborted) → REQUIRES the operator's explicit waiver (step 5).

5. **Gate decision.**
   - **Any PR not MERGED** → the cycle is **delivered, not closed**: stop the close (policy: `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/end/references/delivery-lifecycle.md` — closure always waits for the merge; there is no PR-only completion lane). Print the **delivery handoff**, derived from `python3 "${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_exec_progress.py" "${TARGET_DIR}" --json` (`done`, `total`), the exec receipt's `status`, each review receipt's `status` and `terminal` (located as in step 4b), and pr-scout's quoted JSON (`state`, `isDraft`, `url`), with the owner and remaining action looked up in that reference's state table:
     ```
     ⏸ Delivered, not closed — <TARGET_DIR basename>
     • Executed: <done>/<total> tasks · exec receipt <status>
     • Delivered: <repo>#<n> <OPEN draft | OPEN ready | CLOSED> — review loop <terminal | partial | aborted | not run> · <PR url>
     • Merged: no (<every unmerged PR from pr-scout, with its state>) · Closed: no · Ingested: no
     → Remaining (<owner>): <next from the state table>
     → Resume: after the merge, re-run /cortex:end here
     ```
     Nothing is written — no receipt (the end receipt keeps meaning "every gate passed"), no SESSION-SUMMARY.md, no ingest — and nothing is merged, flipped ready or deployed: delivery alone never authorizes any of them. A cycle whose approved scope ends at the open PR legitimately rests here. For OPEN PRs that still need an updated body or final push before merge, the user can run `/cortex:commit-push-pr` standalone — but do NOT chain into it from here, cortex:end is a verify-only gate.
   - **pr-scout's "Detected PRs" count is 0 AND PLAN.md visibly references PRs** (refs pr-scout reported NOT-A-PR are issues, not PR references) → abort with: `⚠️ pr-scout detected 0 PRs but PLAN.md mentions PRs — detection likely missed a reference form. Inspect PLAN.md manually before re-run.` The user must confirm "yes, this cycle truly has no PRs" via `AskUserQuestion` (header `Gate`) to override.
   - **Any EXEC `- [ ]` open** → abort. Print: `❌ Cannot close cycle. <N> open task(s) in EXEC.md: <list>. Tick or move to "Removed by replan", then re-run.`
   - **state-scout reports DRIFT** → surface, but ask the user whether to proceed (`AskUserQuestion`, header `Gate`): "ai-brain will record state as-of-now, not as-of-PLAN. OK?".
   - **Review required but no evidence (from step 4b)** → the ONLY way through is the operator's explicit waiver. `AskUserQuestion`, naming the situation precisely: *"This cycle changed code/IaC (<PR list or mutation summary>) but the cortex:review loop did not run to completion (<reason: no review receipt | advisor-only — advisor is not review | loop status: partial/aborted>). Review is required unless you explicitly waive it."* Options: `Stop — review first (Recommended)` / `Waive review (operator decision)`. On **stop**: halt with *"Run `/cortex:review` (read-only retroactive pass for already-merged PRs), then re-run /cortex:end."* On **waive**: record it in the receipt (step 8) — the waiver is the operator's deliberate, auditable decision, never a default. Never auto-select, never infer a waiver from conversation tone; only this question's explicit answer counts.
   - All green → proceed.

6. **Distill the session — DO NOT delegate the whole thing to a subagent.** A spawned subagent inherits only its prompt, NOT the conversation. The richest knowledge from the cycle — the in-conversation decisions, surprises, pitfalls the human and the lead figured out together — lives only in the main context. If you spawn a "session-distiller" and just say "read EXP/PLAN/EXEC", ai-brain gets a file summary and misses the session's real learnings.

   **Re-run-after-edit guard.** Before distilling, check whether `${TARGET_DIR}/SESSION-SUMMARY.md` already exists. If yes, this is an "edit-first" re-run from step 7 — the user has manually edited the draft. **Skip distillation entirely, leave the existing file as-is, jump to step 7's lane question — skipping its write** (the ingest-lane question still applies; the draft is never re-distilled or overwritten). Never silently overwrite the user's edits. (The companion case — a stale receipt with `ingested: true` — is already aborted in step 3.)

   **Two-phase distillation (only runs when SESSION-SUMMARY.md does NOT exist):**

   6a. **Lead writes the session learnings inline** — the main context (which actually saw the conversation) drafts the following sections from memory of this very turn-history. No subagent.

      **Compact resilience — read the handoffs first.** Before drafting from memory, glob `${TARGET_DIR}/handoff-*.md`. In long sessions that went through `/compact`, the lead's live memory of early decisions is degraded — but each `/cortex:handoff` file froze the decisions, surprises, and pending items as they stood at that moment. Treat those files as first-class evidence for the sections below, merged with (not replaced by) what the lead still remembers:
      - **Surprises** — things PLAN.md didn't anticipate but came up during execution / late commits / mid-stream pivots. Look at git log of `TARGET_DIR` between the plan and now.
      - **Caveats — which held, which broke** — cross-reference PLAN's Caveats & assumptions against what actually happened.
      - **Pitfalls for next cycle** — what to verify earlier, what assumption tripped us up. These are the highest-value entries for future sessions.

   6b. **Spawn the distiller for file-derived content only.** `Agent(description: "session-distiller", subagent_type: "general-purpose", model: "sonnet", prompt: ...)`. Its prompt carries the lead's 6a draft PLUS instructions to read EXP/PLAN/EXEC and produce the rest:
      - **Problem framed** (1–2 sentences from EXP.md's Problem)
      - **What we shipped** (1 sentence — the outcome, not the activity list)
      - **Key decisions + why** (from PLAN.md's verified facts and resolved questions, preserving every `(resolved by …)` / `(verified by ...)` provenance verbatim)
      - **Concepts / entities** — domain terms, service names, internal patterns the cycle touched. List with one-line definition each so the downstream `ingestor` (invoked by `/cortex:ingest`) can wikify.
      - **Cycle metadata** — TARGET_DIR, PRs merged (list with numbers), date. The distiller must place the HTML comment `<!-- log-only: metadata for log.md, do not wikify into concepts/entities -->` directly under this section's heading — it is the file-level contract the downstream ingestor honors.

      The distiller merges 6a's lead-written sections with its own file-derived sections into a single coherent markdown blob.

   **Content voice — describe the built system, never task status.** SESSION-SUMMARY.md records *what exists now and how it works*: architecture, components and how they interact, key decisions with their why, invariants, pitfalls. Task-status voice is forbidden everywhere outside `Cycle metadata` — no checkbox recaps, no "done / pending / in progress", no "PR merged / awaiting review" narrative, no "verified by lead" progress notes. When drift forces a "Reality at close" section, it describes the **actual state of the system** ("X routes through Y; Z no longer exists"), never the progress of the work ("task 3 was re-validated"). `Cycle metadata` is the single status-bearing section and is log-only downstream (see the 6b marker).

   **If state-scout flagged DRIFT in step 5 and the user proceeded**, the lead must include a "Reality at close" section in 6a noting how live state differed from PLAN.md — and pass that to the distiller so the vault records what's true now, not the superseded plan.

   The output must NOT include: file paths/line numbers, timestamps inside the body, ephemeral state, "successful run" boilerplate.

7. **Save the draft and pick the ingest lane.** Write the distilled content to `${TARGET_DIR}/SESSION-SUMMARY.md`. Then ONE `AskUserQuestion`:
   - **Ingest now (Recommended)** → proceed to step 8 (receipt + closing block), step 9 (cleanup), then tail-chain into `/cortex:ingest` (step 10). The session WILL block during ingestion — that is the trade for closing in one command.
   - **Close now, ingest later** → proceed to steps 8 and 9 exactly as above, but do NOT chain: the closing block ends with the `→ Next: /cortex:ingest` line and the skill stops. This is the lane for taking the long ingestor block in another session (`/cortex:ingest` in a separate `claude` invocation); the receipt stays `ingested: false` until then.
   - **Let me edit first** → end here. Print the path, instruct: *"edit `SESSION-SUMMARY.md`, then run `/cortex:ingest` directly — or re-run `/cortex:end`, which keeps your edits (step 6 guard) and closes. The receipt will be written by `/cortex:ingest` once ingestion lands."*

   Remember the answer as `INGEST_LANE ∈ {now, later, edit}` — steps 8 to 10 branch on it. This skill does NOT invoke the `ingestor` agent. Ingestion is owned by `/cortex:ingest` — running it synchronously there isolates the long block from this skill; the chain hands the session over, it never pulls the work in.

8. **Write gate-passed receipt + closing block.** Author `${TARGET_DIR}/.cortex-end-receipt.json` with `ingested: false` (it'll be promoted to `true` by `/cortex:ingest` once ingestion lands):
   ```json
   {
     "closed_at": "<iso-timestamp from this run>",
     "target_dir": "<abs path>",
     "prs_merged": ["<org>/repo#123", "..."],
     "exec_tasks_done": <int>,
     "review_gate": "passed | n/a (no code/IaC changes) | waived by operator (<reason>)",
     "spawn_fallbacks": [{"agent": "<scout>", "reason": "spawn-infra-error | watchdog-timeout", "resolution": "inline"}],
     "cleanup": {"envs_stopped": ["<dir>"], "envs_left_running": ["<dir>"],
                 "worktrees_removed": [{"repo": "<path>", "path": "<worktree>", "branch": "<name>"}],
                 "skipped": [{"target": "<path|dir>", "reason": "dirty | tip-beyond-merge | down-failed | operator"}]},
     "ingested": false
   }
   ```
   `spawn_fallbacks` is `[]` when every scout/distiller spawn behaved — include the empty array anyway. `cleanup` is written only by step 9 — omit the key entirely when step 9 found nothing to consider (a whole-step operator skip is recorded as `{"skipped": "operator"}`). `review_gate` records step 4b/5's outcome — `passed` from a completed `.cortex-review-receipt.json` (the cortex:review loop; advisor output alone never produces `passed`), `n/a` when the cycle mutated no code/IaC (review not needed), else the operator's explicit waiver with its reason; a waiver is a first-class audit entry, never omitted.
   Then print:
   ```
   ✓ Cycle gates passed at <TARGET_DIR>
   • PRs merged: <count> (<list>)
   • EXEC tasks completed: <done_count>
   • Review gate: <passed | n/a — no code/IaC changes | waived by operator (<reason>)>
   • Drift warnings: <count or "none">
   • Distilled to: <TARGET_DIR>/SESSION-SUMMARY.md
   → <INGEST_LANE = now: "Ingesting now — chaining into `/cortex:ingest` after cleanup." | INGEST_LANE = later: "Next: `/cortex:ingest <folder:...>` to push to ai-brain.">
   ```
   The last line is one of the two, by `INGEST_LANE` — ingest's confirmation will be the last thing on screen when chaining, so the closing block must say what is about to happen.

9. **Cycle cleanup (conditional, warn-and-continue).** Runs immediately after the receipt write — tears down the cycle's local environment(s) and removes the repo worktrees whose PRs the gate just verified as MERGED, so a gate failure never touches them. Every failure prints `⚠ cleanup: <reason>` and continues; the receipt is never rolled back and a cleanup failure never re-opens the close. (When the user exited at step 7 with "Let me edit first", this step never ran — but neither did the receipt write, so the `/cortex:end` re-run that actually closes the cycle reaches this step naturally. No second mechanism.)

   1. **Gather worktree candidates.** Derive the local repo path(s) from EXEC.md's worktree setup tasks (the gate lines of the form `git -C <repo> worktree list`). For each repo, run `git -C <repo> worktree list --porcelain`. A worktree is a removal candidate iff ALL of:
      - its path matches `<repo>/.worktree/*`, or equals the exec receipt's `worktree` (the root the cycle recorded, compared as real paths) and is not the repo's main working tree (the first `worktree list` entry), AND
      - its branch equals the `headRefName` of one of this cycle's MERGED PRs on that repo (from pr-scout's quoted JSON), AND
      - `git -C <worktree> rev-parse HEAD` equals that PR's `headRefOid` — a clean worktree can still carry commits made *after* the merge; tip beyond the merged PR → skip the whole worktree+branch pair with `⚠ cleanup: <branch> has commits beyond the merged PR — left in place`.

      No repo path derivable from EXEC.md → `⚠ cleanup: no repo path derivable from EXEC.md — worktrees left in place` and continue with environments only.
   2. **Gather environment candidates.** Parse the cycle's `SETUP.md` for a `## Local environment` section (format: § "Local environment record" below). Each row is a *recorded* env. Fallback probe: for each worktree candidate whose dir holds a compose file (`docker-compose.yml`, `docker-compose.yaml`, `compose.yml`, `compose.yaml`), run `docker compose ps -q` there — containers running → list it as a *probe-discovered* env (labeled as such; confirm-only, see below).
   3. **Confirm chip.** When anything was gathered, one `AskUserQuestion` listing everything about to be cleaned — envs to stop (recorded and probe-discovered, labeled), worktrees + branches to remove, and what will be skipped (with reasons). Options: `Clean up (Recommended)` / `Skip cleanup`. On skip: record `"cleanup": {"skipped": "operator"}` in the receipt and jump to the summary line. Nothing gathered → no question; print `Cleanup: nothing to clean` and move on.
   4. **Execute — environments first** (an env's cwd may be the worktree about to be removed):
      - Recorded envs: run each row's `down` command in its `dir`; on success delete that row from SETUP.md's `## Local environment` section (the record mirrors reality — remove the section header too when the last row goes). Failure → `⚠ cleanup: down failed for <dir>: <stderr>`; keep the row.
      - Probe-discovered envs: torn down ONLY on the operator's explicit chip confirmation.
   5. **Execute — worktrees.** For each candidate: `git -C <worktree> status --porcelain` non-empty → skip with `⚠ cleanup: <path> dirty — left in place`. Clean → `git -C <repo> worktree remove <path>`, then `git -C <repo> branch -D <branch>`. `-D` (not `-d`) is deliberate and safe here: the merge is gh-verified AND the branch tip equals the merged PR's `headRefOid` (candidate condition), while `-d`'s ancestry check is defeated forever by squash/rebase merges. Never pass `--force` to `worktree remove`.
   6. **Record + summarize.** Update the receipt's `cleanup` block in place (omit the key when nothing was gathered). Then print ONE standalone line — `Cleanup: <N> env(s) stopped · <M> worktree(s) removed · <K> skipped` — do NOT reprint the step-8 closing block.

10. **Tail-chain into `/cortex:ingest` (`INGEST_LANE = now` only).** The single sanctioned `Skill()` call in this skill, and strictly its FINAL act — nothing may follow it. Preconditions, all of them: this run wrote the receipt in step 8 (every gate green — no abort, no stop at step 5), `SESSION-SUMMARY.md` is on disk, step 9 has completed (its prints included), and the lane is `now`. Then:

   ```
   Skill(skill: "cortex:ingest", args: "<folder:<name> when this run was given one, else empty>")
   ```

   Propagate `folder:` verbatim — ingest resolves its own `TARGET_DIR` from its args, so a folder-scoped close must chain a folder-scoped ingest. `⚠` lines from step 9 (dirty worktree left in place, unrecorded env left up, `down` failed) do NOT block the chain: they are housekeeping, recorded in the receipt's `cleanup` block and visible in the transcript above, and none of them changes the distillation. What DOES block: any abort above (gates red, the review-first stop, drift declined) and `INGEST_LANE ∈ {later, edit}` — there the skill ends at its own print and `/cortex:ingest` is the operator's next command.

## Local environment record

A task that starts a long-lived local environment for this cycle (compose stack, dev server, tunnel) records it in the cycle's `SETUP.md`, so step 9 can tear it down at close. The section is optional and created on first use. One table row per environment:

```markdown
## Local environment

| dir | up | down | note |
|-----|----|------|------|
| <repo or worktree path> | <start command, informational> | <teardown command step 9 runs> | <free text> |
```

Step 9 deletes a row once its environment is torn down, so the record mirrors reality.

## Constraints

- **English only in SESSION-SUMMARY.md.** The distillation that feeds ai-brain is in English — concepts, decisions, surprises, caveats, pitfalls all in English. The lead's 6a draft and the distiller subagent's prompt both follow this rule. This keeps the vault consistent and searchable from its `index.md`.
- **SESSION-SUMMARY.md commit follows `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit/references/commit-conventions.md` inline.** When the distillation lands on disk and the user has approved it, the lead reads the conventions reference and drafts the commit per its shape — typical message: `docs(cortex): record SESSION-SUMMARY for <cycle>` with a body citing the PRs merged and the surprises captured. Do NOT call `Skill(cortex:commit ...)` from this skill body (same no-return-semantics trap that cortex:exec documents). If the lead can't draft a confident message, surface the distillation and ask the user to run `/cortex:commit` standalone.
- **Verification is non-optional and never lenient.** Don't produce a SESSION-SUMMARY.md while work is incomplete. The whole point is to land genuine "done" knowledge, not in-progress noise.
- **Review evidence is the cortex:review loop's receipt, not a checkbox and not the advisor.** Step 4b requires a completed `.cortex-review-receipt.json` (located via the exec receipt's `review_receipt` pointer, or directly in the cycle folder for standalone runs) — a hand-ticked "Ship & autonomous review" task is not evidence, and advisor output alone is not review. Review may be skipped ONLY when not needed (no code/IaC/repo mutation → `n/a`) or when the human operator explicitly waives it via the step-5 question — no waiver is ever inferred or auto-selected. Every outcome lands in the end receipt's `review_gate` field.
- **Never fabricate state.** Every PR/EXEC/external check must come from a real tool call quoted in the subagent's report. If something is unverifiable, surface it — do not assume.
- **Delivered is not closed.** An open PR — draft or ready, even when the approved scope ends there — is a delivery checkpoint (`${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/end/references/delivery-lifecycle.md`): the gate stays merge-or-nothing, the refusal is the step-5 delivery handoff, and unmerged-PR evidence never reaches SESSION-SUMMARY.md or ai-brain.
- **Ingestion is out of scope.** Do not invoke the `ingestor` agent, do not write to the ai-brain vault. That belongs to `/cortex:ingest`, which also owns the vault's git lifecycle (worktree → commit → merge → push). This split is load-bearing: it isolates the long synchronous ingestor call from the fast gate-and-distill flow. The tail-chain (step 10) is the sanctioned handoff — `Skill(cortex:ingest)` as the FINAL act — not an ingestor call: the session moves on to `/cortex:ingest`, which still owns every vault read and write.
- **The end→ingest chain is the only `Skill()` call here.** It is reached through the operator's `Ingest now` answer. Never mid-run, never before step 9 has printed, never on an abort. `Close now, ingest later` keeps the take-the-block-elsewhere lane alive.
- **Distill, don't dump.** SESSION-SUMMARY.md is for *future AI retrieval* — favor concepts, decisions, and pitfalls over chronology and file paths. If the distiller produces > ~2 pages, it's over-collecting; tighten.
- **Receipt prevents accidental re-close.** Step 3 always checks `.cortex-end-receipt.json` first; `ingested: true` aborts to prevent re-overwriting an already-ingested cycle.
- **Cleanup is safe-by-construction and never forced.** Step 9 removes a worktree only when its PR is gh-verified MERGED, the checkout is clean, AND the branch tip equals the merged PR's `headRefOid` — dirty worktrees and branches carrying post-merge commits are always left in place with a `⚠`. Unrecorded (probe-discovered) environments are never torn down without the operator's explicit chip confirmation. Cleanup failures are warnings; they never roll back the close.
- `TARGET_DIR` must be inside or equal to CWD. Never write outside (step 9's worktree/env teardown targets repos cited by the cycle's own EXEC.md — the sanctioned exception, mirror of the repos exec itself wrote to).
- Out of scope: replanning, ticking missed tasks, merging PRs, fixing the cycle, ingestion. cortex:end only verifies and distills — fixing belongs upstream (re-run cortex:plan, finish the work); ingestion belongs to `/cortex:ingest`.
