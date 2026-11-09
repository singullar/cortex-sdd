---
name: ingest
description: Use when the user has a finalized SESSION-SUMMARY.md and wants to push it into ai-brain — phrases like "cortex:ingest", "send it to the brain", "ingest the summary", "ingest now". Reads `SESSION-SUMMARY.md` from the target folder, prepares a per-run worktree of the ai-brain vault (`scripts/cortex_ingest_git.py` — baseline-commits a dirty vault, fast-forwards from the remote), invokes the `cortex:ingestor` agent synchronously INTO that worktree (this WILL block the session until ingestion completes — by design, the long step is isolated to this skill), then commits the ingest with `Cortex-Cycle` trailers naming the originating cycle, merges it locally into the vault's checked-out branch (index.md/log.md conflicts auto-resolve by union, wiki conflicts are resolved by the lead), pushes when the vault has a remote (warn-and-continue otherwise), cleans up, and writes `.cortex-end-receipt.json` with the vault paths plus a `git` block. Legacy in-place ingestion when the vault is not a git repo. Refuses if the cycle isn't gated/distilled yet (no SESSION-SUMMARY.md → run `/cortex:end` first) or no vault resolves (→ run `/cortex:install`).
---

# Skill: cortex:ingest

The dedicated ingestion step of the Spec-Driven Development pipeline. Split out from `/cortex:end` so the long-running ingestor call is isolated from the gate-and-distill flow. It is reached two ways: a clean `/cortex:end` tail-chains into it as its final act, on the operator's `Ingest now` answer, or the operator invokes it directly (after `Close now, ingest later`, after editing `SESSION-SUMMARY.md`, or to resume an unlanded ingest).

**To truly free your working session during ingestion**, answer `Close now, ingest later` at `/cortex:end` and run `/cortex:ingest` in a separate `claude` invocation (a new terminal tab or window). This skill still blocks synchronously inside whichever session runs it — the split just isolates *which* session takes the block.

This is the **only** point at which session knowledge enters ai-brain. The `cortex:ingestor` agent owns vault **content** rules (.rules/ingestion.md, frontmatter, wikilinks, lint); **the lead owns the vault's git lifecycle** — worktree, commit, merge, push, cleanup — through `scripts/cortex_ingest_git.py`. Knowledge is not "in the brain" until it is merged into the vault's branch and pushed.

## Roles — lead + single subagent (no Team Agents)

Lead calls the `cortex:ingestor` agent **once**, **unnamed** (role in `description:`, never `name:` — a named spawn becomes an in-process teammate that hangs the lead in teams-enabled sessions and is invisible to `TaskList`/`TaskStop`, defeating the watchdog below). No `TeamCreate`, no `team_name`, never a parallel/fan-out ingest. The ingestor runs with its own model/behavior — do not override. If a run fails, retry the **same** single ingestor call — never escalate to a team.

The ingestor (`agents/ingestor.md`, shipped in this plugin) is a **pure content agent**: given a vault root it writes vault artifacts and runs the vault linter (`scripts/cortex_vault_lint.py`). It never runs a git command — the lead does, via the script. This split (deterministic git in the lead, none in the subagent) is deliberate: a subagent managing worktrees is fragile, and the spawn prompt pinning the vault root is all the ingestor needs to know about the lane.

## Input

Free-text `args`. Optional tokens, rest ignored:
- `folder:<name>` — target subfolder (relative to CWD; reject `..` or absolute).
- `vault:<abs-path>` — explicit ai-brain vault root. Wins over every other source. The escape hatch for folders that carry no `.claude/settings.json` stamp.

Examples:
- (no args) → ingest from CWD.
- `folder:tuesday-deploys` → ingest from that subfolder's session.
- `folder:tuesday-deploys vault:/abs/path/to/brain` → same session, vault pinned explicitly.

## Steps

1. **Parse args.** Strip `folder:<name>` (reject `..`, abs paths) and `vault:<path>`. `TARGET_DIR = ${CWD}/${folder}` or `${CWD}`. `SCRIPT="${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/scripts/cortex_ingest_git.py"`.

2. **Verify the artifact.** `SESSION-SUMMARY.md` must exist in `TARGET_DIR`. If missing, abort: *"cortex:ingest requires SESSION-SUMMARY.md in <TARGET_DIR>. Run `/cortex:end` first — that gates the cycle and produces the distillation this skill ingests."*

3. **Idempotency / resume check.** Read `${TARGET_DIR}/.cortex-end-receipt.json` if present.
   - **`git.status` is `conflicts`, `aborted` or `unmerged`** → a previous run ingested into a worktree but never landed. **Do NOT re-run prepare or the ingestor** — that would create a second worktree with duplicate content and orphan the first branch. Resume with the receipt's `git.branch` / `git.worktree` / `git.vault`: `aborted`/`unmerged` → step 6 (`land`; if the worktree no longer exists but the branch does, it still resumes from the committed branch). `conflicts` → the merge was left in progress: if `git -C <vault> rev-parse -q --verify MERGE_HEAD` succeeds go to step 7 (resolve the receipt's `git.conflicts`, then `continue` — it inherits the `lead-resolving` lock); if the merge is gone (someone aborted by hand) go to step 6.
   - **`ingested` is `true`** → the session was already ingested. Show the user the prior `closed_at` + `vault_paths` + `git.merge_commit`, and via `AskUserQuestion`:
     - **Skip (abort)** — default. The vault has it; do nothing.
     - **Re-ingest (overwrite receipt + append to ai-brain)** — proceed to step 4, knowing the ingestor will create new entries or update existing ones per `.rules/ingestion.md`.
   - **`ingested: false`** (normal post-`/cortex:end` state) or **no receipt at all** (the user picked "edit first" in `/cortex:end`) → proceed to step 4; `SESSION-SUMMARY.md` was verified in step 2.

4. **Prepare the vault git lane.** One Bash call:
   ```bash
   python3 "$SCRIPT" prepare --target-dir "$TARGET_DIR" --cwd "$PWD" --data-dir "${CLAUDE_PLUGIN_DATA}" [--vault "<vault arg>"]
   ```
   The script resolves the vault (`vault:` arg → `cortexAiBrainPath` in `${TARGET_DIR}/.claude/settings.json` → `${CWD}/.claude/settings.json` → the `ai-brain vault for this project:` line of the session CLAUDE.md → the config in `--data-dir` recorded by `/cortex:install`), then — under the vault's ingest lock — prunes stale worktrees, excludes `.worktrees/` and sets `index.md`/`log.md` to `merge=union` via `.git/info/*` (local-only, never tracked), **baseline-commits anything dirty in the vault's main checkout** (`:chore: baseline pending vault changes` — never asks: those are earlier ingests that were never committed), fetches + fast-forwards the checked-out branch from the remote when one exists, and creates the worktree `<vault>/.worktrees/ingest-<YYMMDD-HHMM>-<cycle-slug>` on branch `ingest/<YYMMDD-HHMM>-<cycle-slug>`.

   Branch on the JSON:
   - `mode: "worktree"` → `VAULT_ROOT=<worktree>`, `VAULT_MAIN=<vault>`. Print one line: `prepared <branch> (baseline: <sha7|none>, ff: yes|no)` plus any `warnings` (`no-remote`, `fetch-failed`, `diverged` are informational).
   - `mode: "legacy"` with an empty `vault` → no vault resolved. Refuse: print *"No ai-brain vault resolved for this cycle. Run `/cortex:install` to configure one, or pass `vault:<path>`."* and stop. Write nothing.
   - `mode: "legacy"` with a `vault` → the vault is not a git repo. Print `⚠ vault git lifecycle skipped: <reason> — ingesting in place` and continue: `VAULT_ROOT` = that vault path, no land step, receipt `git.mode: "legacy"`.
   - exit **3** (`detached-head`, `foreign-operation`, `lock-timeout`, `git-failed`) → hard stop. Print the JSON's `error` verbatim and *"needs human: fix the vault checkout, then re-run /cortex:ingest"*. Write nothing.

5. **Invoke the `cortex:ingestor` agent — synchronously, into the worktree.** This is the long step. The session WILL block here. That isolation is the entire point of splitting from `/cortex:end`. (When resuming from step 3, skip this step and go to 6.)

   ```
   Agent(
     subagent_type: "cortex:ingestor",
     description: "vault-ingestor",
     prompt: "Vault root for THIS run: <VAULT_ROOT>. `cd` there first — every read (CLAUDE.md, .rules/, index.md, log.md) and every write (raw/, wiki/, index.md, log.md, lint report) is relative to that path. Never write to <VAULT_MAIN>. Do NOT run any git command — the caller owns commit, merge and push.\n\nIngest the following session summary into ai-brain following .rules/ingestion.md. The source is a Spec-Driven Development cycle at <TARGET_DIR>. Generate the standard wiki artifacts (source, concepts, entities) plus index.md and log.md updates, then run the vault linter. The 'Cycle metadata' section is marked log-only — it feeds the log.md row and the source file's metadata ONLY; never create concepts or entities from it. Content follows:\n\n<contents of SESSION-SUMMARY.md>"
   )
   ```

   Capture the returned vault paths (raw clipping, source, concepts created/updated, entities created/updated).

   **Spawn contract — watchdog + takeover.** A hung ingestor must not strand the lead:
   - **Infra error on spawn** (tmux pane failure, model pin that does not resolve): retry the same single `Agent(...)` call **once**.
   - **Watchdog (background mode only).** If the harness runs the spawn in the background, a bare "stop at 15 min" never fires (the lead only wakes on a *completion* notification a hung ingestor never sends). Arm a one-shot wake alongside the spawn — `Bash("sleep 900; echo INGEST-TIMEBOX", run_in_background: true)`. If the timer wins and the ingestor is still running with no output, `TaskList` → `TaskStop` it, then retry the same single call once. In inline (blocking) mode this is a no-op — the call returns on its own. **Stop a hung ingestor BEFORE step 6 touches the worktree** — an agent waking mid-commit corrupts the merge.
   - **Record any fallback** in the receipt's `spawn_fallbacks` field (step 8). If both attempts fail, surface the failure to the user — do NOT fake a receipt. In `worktree` mode no `abort` is needed (nothing was merged) — leave the worktree in place for the retry and say so.

6. **Land the ingest.** Skip in `legacy` mode. One Bash call:
   ```bash
   python3 "$SCRIPT" land --vault "$VAULT_MAIN" --branch "<branch>" --worktree "<worktree>" --target-dir "$TARGET_DIR"
   ```
   The script verifies where the ingestor wrote, commits the worktree (`:add: ingest <cycle-slug>` + trailers `Cortex-Cycle` and `Cortex-Cycle-Path` — `git log --format='%(trailers:key=Cortex-Cycle)'` is the tracking query), then under the lock fetches + fast-forwards the target branch, merges the ingest branch (fast-forward when possible; `index.md`/`log.md` conflicts auto-resolved by union), pushes (rejected → fetch, reconcile-merge, one retry; no remote → warning), removes the worktree + branch, releases the lock.

   Branch on the exit code:
   - **0** `status: "landed"` → go to step 8. Note `mode` — `"in-place-fallback"` means the ingestor ignored the pin and wrote into the main checkout; the script committed it there in place (still under the lock, still trailered, still pushed). Surface that as a ⚠ line.
   - **4** `status: "conflicts"` → **first** write the receipt (step 8 shape) with `ingested: false`, `git.status: "conflicts"`, `git.branch`/`git.worktree`/`git.vault` and `git.conflicts` (the list) — so a session dying mid-resolution resumes at step 7 instead of stalling on the held lock — then step 7.
   - **5** `status: "nothing-written"` → the ingestor produced nothing (neither worktree nor main checkout changed). Retry the single ingestor call once (record `spawn_fallbacks: [{reason: "nothing-written", resolution: "retried-inline"}]`), then re-run `land`. A second exit 5 is a hard stop: surface it, leave the worktree for a manual look, never fake a receipt.
   - **3** → hard stop, print the JSON's `error`. If the ingest branch exists (`git -C "$VAULT_MAIN" branch --list "<branch>"` non-empty), write the receipt with `ingested: false` and `git.status: "unmerged"` + `git.branch`/`git.worktree`/`git.vault` so the next run resumes at land per step 3; otherwise write nothing.

7. **Lead-resolved conflicts** (exit 4 only). The merge is left in progress in `VAULT_MAIN` and the lock is held for you. For **each** path in `conflicts` (always `wiki/**` — `index.md`/`log.md` were already unioned): read both sides — `git -C "$VAULT_MAIN" show :2:<path>` (the vault's branch) and `git -C "$VAULT_MAIN" show :3:<path>` (this ingest) — and write the merged file: keep every fact and every `## Sources` link from BOTH sides, drop nothing, no conflict markers, English. Vault files are append-only by rule, so the merge is a union of sections, not a choice. Then:
   ```bash
   python3 "$SCRIPT" continue --vault "$VAULT_MAIN" --branch "<branch>" --worktree "<worktree>" --target-dir "$TARGET_DIR" --resolved <path> [--resolved <path> ...]
   ```
   Exit 0 → step 8 (the result carries `conflicts_lead_resolved`). Exit 4 again → markers or unstaged paths remain; fix and re-run `continue`. If a path genuinely cannot be merged (binary, incoherent content):
   ```bash
   python3 "$SCRIPT" abort --vault "$VAULT_MAIN" --branch "<branch>" --worktree "<worktree>" --target-dir "$TARGET_DIR" --reason "<why>"
   ```
   which aborts the merge, releases the lock and **keeps the ingest branch + worktree** (the work is safe). Write the receipt with `ingested: false`, `git.status: "aborted"`, `git.branch`, `git.worktree`, `git.resume` (the command the JSON returns), print *"needs human: <reason> — re-run /cortex:ingest to retry the merge, or land by hand: <resume>"* and stop.

8. **Write/update the receipt** at `${TARGET_DIR}/.cortex-end-receipt.json`. If a prior receipt from `/cortex:end` exists, preserve its fields (`prs_merged`, `exec_tasks_done`, `closed_at`, `review_gate`, `cleanup`) and merge in the ingestion fields. If no prior receipt exists (user took the `/cortex:end` "edit first" path), author the receipt fresh — leave `prs_merged`/`exec_tasks_done` as empty/zero with a note, or fetch them by re-parsing PLAN.md/EXEC.md (cheap).
   ```json
   {
     "closed_at": "<from prior receipt OR iso-timestamp of this run if no prior>",
     "ingested_at": "<iso-timestamp of this run>",
     "target_dir": "<abs path>",
     "ingested": true,
     "spawn_fallbacks": [{"reason": "spawn-infra-error | watchdog-timeout | nothing-written", "resolution": "retried-inline"}],
     "vault_paths": {
       "raw": ["raw/clippings/..."],
       "source": ["wiki/sources/..."],
       "concepts_created": ["wiki/concepts/..."],
       "concepts_updated": ["wiki/concepts/..."],
       "entities_created": ["wiki/entities/..."],
       "entities_updated": ["wiki/entities/..."]
     },
     "git": {
       "mode": "worktree | in-place-fallback | legacy",
       "status": "landed | unmerged | aborted",
       "vault": "<abs vault path>",
       "target_branch": "main",
       "branch": "ingest/<stamp>-<slug>",
       "worktree": "<abs path (kept only when unlanded)>",
       "base_sha": "<sha>",
       "baseline_commit": "<sha or null>",
       "ingest_commit": "<sha>",
       "merge_commit": "<sha of the target branch after landing>",
       "fast_forward": true,
       "pushed": true,
       "remote": "origin | \"\"",
       "conflicts_auto_resolved": [],
       "conflicts": ["wiki/concepts/... (only while status is conflicts)"],
       "conflicts_lead_resolved": ["wiki/concepts/..."],
       "warnings": ["no-remote: push skipped"],
       "resume": "<land command, only when unlanded>"
     }
   }
   ```
   `conflicts_auto_resolved` is usually empty: the `merge=union` driver resolves `index.md`/`log.md` silently inside `git merge`, so they never surface as conflicts — the field only lists them when the script's fallback union had to run.

   **`ingested: true` ⇔ the content landed** — merged into the target branch (`worktree`), committed in place (`in-place-fallback`), or plainly ingested (`legacy`). A `pushed: false` with a warning is still landed (the knowledge is on the branch; the push is retried by the next ingest's fetch/ff + push). `unmerged`/`aborted` keep `ingested: false` so `/cortex:status` keeps showing `ingest pending` and step 3 resumes at land.

9. **Print confirmation:**
   ```
   ✓ Ingested SESSION-SUMMARY.md → ai-brain
   • Source:    wiki/sources/<slug>.md
   • Concepts:  <N> created, <M> updated
   • Entities:  <N> created, <M> updated
   • Vault git: merged ingest/<stamp>-<slug> → <target> @<sha7> · pushed ✓ | not pushed (no remote) | push rejected ⚠
                (baseline: <sha7|none> · conflicts: <k> auto-unioned, <j> lead-merged | none)
   • Receipt:   <TARGET_DIR>/.cortex-end-receipt.json
   → Now in the vault's index.md: <top 3 concepts the ingestor reported>
   ```
   In `in-place-fallback` mode the Vault git line reads `⚠ ingestor wrote into the main checkout — committed in place @<sha7>`; in `legacy` mode `⚠ no git lifecycle (<reason>)`.

## Constraints

- **Synchronous on purpose.** The session blocks during ingestion. Do not pass `run_in_background: true` — this skill exists precisely so the user can decide WHEN to take the block, and the land + receipt steps depend on the ingestor's return. Backgrounding would leave the vault unmerged and the receipt unwritten, breaking the `/cortex:end` step 3 idempotency check.
- **Ingest in a worktree, merge into the vault's own branch, push — never a PR.** The lead creates a per-ingest worktree, the ingestor writes into it, the lead commits/merges/pushes through `scripts/cortex_ingest_git.py`. The vault's *checked-out* branch is the target (`main`, `master`, whatever) — never hardcoded, never switched. Never `--force` push, never `-X theirs`. This is the single sanctioned write outside CWD.
- **Delegate all vault *content* to `cortex:ingestor`; the lead owns only *git*.** The ingestor never runs git (a git command from it is a bug); the lead never hand-writes vault files — with ONE exception: step 7's conflict merge, where the lead unions the two sides of a `wiki/**` file.
- **No vault, no ingest.** When no source in step 4's order resolves a vault, the skill refuses and points to `/cortex:install`. In-place ingestion exists only for a resolved vault that is not a git repo.
- **A dirty vault checkout is baselined, never asked about.** Uncommitted files in the vault's main checkout are earlier ingests that never got committed. `prepare` commits them as `:chore: baseline pending vault changes` (untracked included).
- **Parallel ingests are supported — serialize only the main checkout.** Concurrent `/cortex:ingest` runs each get their own worktree/branch; the vault's main checkout is mutated only under the script's mkdir-lock (`<vault>/.git/cortex-ingest.lock`, stale-broken by age/dead pid). A run that cannot get the lock leaves its branch committed-but-unmerged and exits 3 — loud and non-destructive; the receipt's `git.status: unmerged` makes the next run resume at land.
- **Conflicts are resolved, never guessed.** `index.md`/`log.md` are append-only → union (both sides kept; ordering is cosmetic, a later lint normalizes). Any other conflict is handed to the lead with the merge left in progress and the lock held; unresolvable → `abort` (branch + worktree kept, receipt `aborted`, needs human). Never a blind ours/theirs.
- **Receipt is authoritative.** It's how `/cortex:end` step 3 detects "already ingested", how `/cortex:status` reports the closed state, and how step 3 here resumes an unlanded ingest.
- **Never re-write SESSION-SUMMARY.md.** This skill is read-only on that file. Edits belong upstream in `/cortex:end` (or by the user's hand between `/cortex:end` and `/cortex:ingest`).
- **English only** in any output written to disk (vault entries via ingestor, commit messages — the vault's `.rules/git.md` `:type:` grammar — and receipt fields). Regardless of conversation language.
- `TARGET_DIR` (and the receipt) must be inside or equal to CWD. The **only** sanctioned write outside CWD is the ai-brain worktree/commit/merge/push above.
- **Out of scope**: gating (PRs, EXEC ticks, drift), distillation, replanning. Those belong to `/cortex:end`.
