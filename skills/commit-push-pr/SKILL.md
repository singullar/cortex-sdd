---
name: commit-push-pr
description: Use when shipping a Spec-Driven Development worktree to GitHub in one shot — phrases like "cortex:commit-push-pr", "commit push pr", "open the PR", "ship this cycle", "send it for review", "push and open a PR". Commits any pending changes following the `/cortex:commit` conventions inline (English, conventional header, multi-line semantic body), pushes the branch (`-u` on first push), and opens a PR via `gh pr create` with a semantic multi-line description that cites PLAN.md/EXEC.md when present. Prefer this skill over a generic commit-and-PR skill whenever the cycle context applies, so the PR history stays uniform and audit-friendly.
---

# Skill: cortex:commit-push-pr

Goes from a working `.worktree/<prefix>-<slug>/` to an open PR in one pass. Inlines the `/cortex:commit` conventions and `gh pr create` so the whole flow runs without chaining into other skills mid-run (which would hit the no-return-semantics trap documented across this plugin).

## When to use vs. neighbors

- Use this skill when there's a worktree (or feature branch) that should land as a PR — typically the final move after `/cortex:exec` finishes a phase set.
- Use `/cortex:commit` alone when the user wants to commit but not push (e.g. mid-phase staging).
- Use `/cortex:end` when the user wants to verify the PR is merged and distill the cycle into SESSION-SUMMARY.md — that's a downstream step.

## Inputs (mostly inferred)

- `REPO_DIR`: defaults to CWD. If CWD is inside a `.worktree/<...>/`, that's the working dir for every step. Reject if not a git repo.
- `BASE_BRANCH`: defaults to the remote default (`origin/HEAD` → strip `origin/`); falls back to `main` if unresolved.
- `PR_TITLE` / `PR_BODY` (optional overrides): if the user pasted a draft, use it verbatim after a sanity check (English, multi-line body).
- `DRAFT` (optional flag): when the user says "draft PR" / "open as draft", pass `--draft` to `gh pr create`.

## Steps

1. **Gather context (single bash call).** Quote relevant lines back into reasoning — do not push or open a PR without first seeing the diff.

   Resolve the base branch first so `git log` has a real range; falling through to `..HEAD` silently yields empty output (a footgun — the log fallback never fires).

   ```bash
   cd "${REPO_DIR}" && \
     BASE="$(git rev-parse --abbrev-ref origin/HEAD 2>/dev/null | sed 's|origin/||')" && [ -n "$BASE" ] || BASE="main" && \
     printf '=== STATUS ===\n'         && git status && \
     printf '\n=== DIFF (HEAD) ===\n'   && git diff HEAD && \
     printf '\n=== BRANCH ===\n'        && git branch --show-current && \
     printf '\n=== UPSTREAM ===\n'      && (git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo 'NO_UPSTREAM') && \
     printf '\n=== LOG vs BASE (%s) ===\n' "$BASE" && (git log --oneline "${BASE}..HEAD" 2>/dev/null || git log --oneline -10) && \
     printf '\n=== DEFAULT_BRANCH ===\n' && echo "$BASE" && \
     printf '\n=== EXISTING_PR ===\n'   && (gh pr view --json url,state,title,baseRefName 2>/dev/null || echo 'NO_OPEN_PR')
   ```

   Decision tree from the output (all paths terminate at step 5/6 — refresh-mode still goes through drafting at step 4):
   - **No branch (detached HEAD)** → halt, tell the user to run `/cortex:new-worktree` (do NOT invoke it here).
   - **On `main` / default branch** → run the inline worktree-chain in step 1a, then re-enter this decision tree from inside the new worktree. Do not push directly to default. PRs from `main → main` are not a thing.
   - **Clean tree AND all commits pushed AND no open PR** → nothing to commit, nothing to push, PR needs creating. Skip to step 4 (draft) → step 5 (create).
   - **Clean tree AND all commits pushed AND open PR exists** → ask via `AskUserQuestion`: `Refresh PR title/body` / `Leave as-is — stop` / `Cancel`. On refresh, skip to step 4 (draft new body) → step 5 in refresh mode (`gh pr edit`). The draft step is non-skippable: step 5 needs the title/body that step 4 produces.
   - **Working tree dirty** → proceed to step 2.
   - **Unpushed commits with clean tree** → skip step 2, go to step 3.

1a. **Inline worktree-chain — ONLY when step 1 routed here from "On `main`".** Skip this step entirely on every other branch path.

   The `Skill` tool cannot dispatch `cortex:new-worktree` mid-run without losing control (the no-return-semantics trap documented across this plugin). So inline the same logic that `cortex:new-worktree` would run: stash any WIP (including untracked files), create the worktree off freshly-pulled `main`, cd into it, restore the stash there, then resume this skill from step 1 (re-gather context — the new CWD has its own status/branch/upstream).

   Infer `BRANCH_PREFIX` (`feat` / `chore` / `fix`) and a kebab-case `FEATURE_SLUG` from the pending diff and conversation context. If either is genuinely ambiguous, ask via `AskUserQuestion` (one question per missing value, headers `Prefix` and `Slug`) — do not invent a slug from thin air.

   ```bash
   cd "${REPO_DIR}" && \
     STASH_MSG="cortex:commit-push-pr autochain ${BRANCH_PREFIX}-${FEATURE_SLUG} $(git rev-parse --short HEAD)" && \
     git stash push -u -m "${STASH_MSG}" && \
     git fetch --prune origin && \
     git checkout main && \
     git pull --ff-only origin main && \
     mkdir -p .worktree && \
     ( grep -qxE '\.worktree/?' .gitignore 2>/dev/null || printf '.worktree/\n' >> .gitignore ) && \
     git worktree add -b "${BRANCH_PREFIX}/${FEATURE_SLUG}" ".worktree/${BRANCH_PREFIX}-${FEATURE_SLUG}" main && \
     cd ".worktree/${BRANCH_PREFIX}-${FEATURE_SLUG}" && \
     git stash pop
   ```

   Hard failures abort the whole skill with the raw git output — do NOT swallow them:
   - `git pull --ff-only` fails (local `main` diverged) → user must reconcile manually.
   - `git worktree add` collision (slug already taken) → ask the user for a new slug, retry.
   - `git stash pop` conflict → leave the stash on the stack, surface `git stash list` output, halt.

   After this step succeeds, set `REPO_DIR` to the new worktree path for every subsequent step and go back to step 1 to re-gather context with the correct branch/upstream — then continue down the decision tree.

2. **Commit pending changes — follow the Cortex commit conventions inline.** Read `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit/references/commit-conventions.md` (the single source of truth shared with `/cortex:commit`) and apply it verbatim: message shape, allowed types, scope/subject/body rules, English-only, split heuristic, mechanics (heredoc, no `--no-verify`, no `--amend` on hook-fail, no `git add -A`).

   The reference file is the authoritative spec — do not paraphrase or re-derive the rules here. If the diff is ambiguous (multiple unrelated changes that need user input to scope) and the lead can't draft a confident message, pause and ask the user to run `/cortex:commit` standalone, then resume this skill from step 3.

3. **Push.** First push of a new branch needs `-u`; subsequent pushes don't:

   ```bash
   # If UPSTREAM was NO_UPSTREAM:
   cd "${REPO_DIR}" && git push -u origin HEAD

   # Otherwise:
   cd "${REPO_DIR}" && git push
   ```

   If the push is rejected (non-fast-forward, hook block, auth), surface the actual git output. Do NOT force-push. If the user explicitly asks for a force-push, prefer `--force-with-lease` over `--force` and confirm via `AskUserQuestion` first.

4. **Draft the PR title and body — follow the Cortex PR conventions inline.** Read `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit-push-pr/references/pr-conventions.md` (the single source of truth shared with `cortex:exec`'s ship step and `cortex:review`) and apply it verbatim: title rules, semantic multi-line body template, section adaptation, mechanics, worked example.

   The reference file is the authoritative spec — do not paraphrase or re-derive the rules here. Refresh mode is covered there too (diff the new body against `gh pr view --json body --jq .body` and tell the user what changed before applying).

5. **Open the PR (or refresh it).** Pass the body via stdin so newlines and special characters survive shell escaping:

   ```bash
   # NEW PR:
   cd "${REPO_DIR}" && gh pr create \
     --base "${BASE_BRANCH}" \
     --head "$(git branch --show-current)" \
     --title "<TITLE>" \
     --body-file - <<'EOF'
   ## Summary
   <body content here>

   ## Why
   ...
   EOF
   ```

   ```bash
   # REFRESH EXISTING PR:
   cd "${REPO_DIR}" && gh pr edit --title "<TITLE>" --body-file - <<'EOF'
   <new body content>
   EOF
   ```

   Add `--draft` to `gh pr create` when the user asked for a draft PR.

   On `gh` errors (auth, repo missing, no GitHub remote), surface the error verbatim — do NOT silently retry. If the repo has no GitHub remote, halt and ask the user how to proceed (no PR can be opened from a local-only repo).

6. **Confirm and print.** After success, fetch the URL for the user:

   ```bash
   cd "${REPO_DIR}" && gh pr view --json url,number,state --jq '.'
   ```

   Print to the user:

   ```
   ✓ PR ready
   • # <num>  <type>(<scope>): <subject>
   • State:   OPEN | DRAFT
   • URL:     <url>
   → Next: /cortex:end once the PR merges, to verify + distill the cycle.
   ```

## Constraints

- **Commit conventions come from `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit/references/commit-conventions.md`** — English-only, mandatory multi-line body, no `--no-verify`, no `--amend` on hook-fail, no `git add -A`, branch guards. This skill does not restate them; read the reference and apply.
- **PR conventions come from `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit-push-pr/references/pr-conventions.md`** — title rules, semantic multi-line English body, section adaptation, worked example. This skill does not restate them; read the reference and apply. A header-only PR ages into "trust me."
- **No `--force` (use `--force-with-lease` only with explicit confirm).** Force-push without lease can silently overwrite a teammate's work even on a feature branch.
- **No direct push or PR from `main` / default branch.** When CWD lands on `main`, step 1a inlines the worktree-chain (stash → checkout-pull-main → worktree add → cd → stash pop) and the skill resumes from inside the new worktree. The `Skill` tool is NOT used for this — see the next constraint.
- **No `Skill(...)` chaining mid-run.** Inline the conventions from the reference instead. Two reasons: (a) `Skill()` has no return semantics, so chaining into a Cortex skill from inside another ends the calling skill; (b) duplication-via-reference keeps the rules in exactly one file, eliminating drift.
- **Never fabricate references.** Cite PLAN.md/EXEC.md sections, ticket IDs, or commit hashes only when verifiably present. Inventing them is the worst failure mode for a PR body — it survives review and rots silently.
- **`gh` errors are surfaced verbatim, not retried.** Auth failures, missing repo, no GitHub remote — each needs a user decision, not a guess.

## Out of scope

- **Merging the PR.** Out of scope — that's a human (or CI) decision. `/cortex:end` only verifies merged state; it does not merge.
- **Requesting reviewers / setting labels / milestones.** Add by hand or via a follow-up `gh pr edit` — keeping this skill narrow.
- **Creating the worktree from scratch with full interactive prompts.** That's `/cortex:new-worktree`. This skill only inlines the minimal worktree-chain in step 1a when CWD is on `main` — it does not scaffold standalone worktrees on demand.
- **Distilling the cycle into SESSION-SUMMARY.md.** That's `/cortex:end`.
- **Forced history rewrites** (rebase, squash, amend across pushed commits). Out of scope — the skill produces forward-only commits and forward-only pushes.
