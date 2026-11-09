---
name: commit
description: >-
  Use when committing work produced inside a Spec-Driven Development cycle — explicit phrases like "cortex:commit", "commit this cortex cycle", "commit this phase", "commit the EXEC.md changes", "wrap this worktree with a commit", or any time the active session is operating under cortex:* artifacts (EXP.md/PLAN.md/EXEC.md present, working inside a `.worktree/<prefix>-<slug>/`, or follow-up to /cortex:exec). Produces conventional-commit messages in English with a `type(scope): subject` header AND a semantic multi-line body that explains the *why* of the change. Refuses single-line commits and non-English text. Prefer this skill over a generic commit skill whenever the cycle context applies, so the cycle's audit history stays uniform and ai-brain-ingestible.
---

# Skill: cortex:commit

Crafts clean, conventional, multi-line git commits from the current working tree. The full rule set lives in `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit/references/commit-conventions.md` — the single source of truth shared by every Cortex skill that touches git.

## Inputs

Most inputs are inferred — the user usually just says "commit this." When invoked:

- `REPO_DIR`: defaults to CWD; if inside a `.worktree/<...>` directory (per `cortex:new-worktree`), use that. Reject paths outside a git repo.
- `TYPE` (optional override): one of the conventional types listed in the reference. If omitted, infer from the diff.
- `SCOPE` (optional override): a short kebab-case noun naming the area touched (e.g. `auth`, `cortex:exec`, `worktree-skill`). If omitted, infer from the dominant file path.
- `SPLIT` (optional): `single` (default) or `split` to force one commit per logical group of files.

## Steps

1. **Read the conventions reference first.** Open `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit/references/commit-conventions.md` and hold it in context for the rest of the skill — it defines message shape, allowed types, scope rules, subject/body rules, English-only rule, split heuristic, mechanics (heredoc, no `--no-verify`, no `--amend` on hook-fail, no `git add -A`), and worked examples. The body below references it; do not re-derive the rules.

2. **Gather context (single bash call).** Quote the output back into reasoning — never commit without reading the diff first.

   ```bash
   cd "${REPO_DIR}" && \
     printf '=== STATUS ===\n'  && git status && \
     printf '\n=== DIFF ===\n'   && git diff HEAD && \
     printf '\n=== BRANCH ===\n' && git branch --show-current && \
     printf '\n=== LOG ===\n'    && git log --oneline -10
   ```

   - **Clean tree** → tell the user "nothing to commit" and stop.
   - **Detached HEAD** → surface and ask whether to cut a branch via `/cortex:new-worktree` first. Do not commit blindly into a detached HEAD.

3. **Apply the branch guard** for `main` / `master` / default branch — see the "Branch guards" section of the reference. Warn-and-ask, do not refuse. On "Switch to a new worktree first": run the inline worktree-chain in step 3a, then resume at step 4 from inside the new worktree. Do NOT invoke `cortex:new-worktree` via the `Skill` tool — the no-return-semantics trap kills this skill mid-flight.

3a. **Inline worktree-chain — ONLY when step 3 routed here from "Switch to a new worktree first".** Skip on any non-main branch.

   Inline the same logic that `cortex:new-worktree` would run (the `Skill` tool cannot dispatch it without losing control mid-run). Infer `BRANCH_PREFIX` (`feat` / `chore` / `fix`) and a kebab-case `FEATURE_SLUG` from the staged diff and conversation context; if either is genuinely ambiguous, ask via `AskUserQuestion` — do not invent a slug from thin air.

   ```bash
   cd "${REPO_DIR}" && \
     STASH_MSG="cortex:commit autochain ${BRANCH_PREFIX}-${FEATURE_SLUG} $(git rev-parse --short HEAD)" && \
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

   Hard failures abort the whole skill with the raw git output: `pull --ff-only` divergence, `worktree add` collision, `stash pop` conflict. After success, set `REPO_DIR` to the new worktree path for the remaining steps.

4. **Decide split vs single commit** per the reference's "Split vs single commit" heuristic. Default to single when in doubt.

5. **Infer `type` and `scope`** from the diff (unless the user passed overrides), following the "How to pick" sections of the reference.

6. **Draft the message** per the reference's "Message shape" — header + blank line + multi-line body, English, subject ≤72 chars imperative, body always present and semantic. Translate intent from any non-English conversation into English — do not paste the user's prompt verbatim.

7. **Stage and commit each group** using the heredoc form documented in "Mechanics." Stage by file name (never `git add -A`). On pre-commit hook failure: read the hook output, fix the root cause, re-stage, retry as a new commit — no `--no-verify`, no `--amend` of a hook-rejected commit.

8. **Confirm.** Run:

   ```bash
   cd "${REPO_DIR}" && git log -1 --format='%h %s' && git status
   ```

   Print to the user:

   ```
   ✓ Committed <N> change-set(s) on <branch>
   • <hash> <type>(<scope>): <subject>
   → Working tree: clean | <X> files still modified
   ```

## Out of scope

- **Pushing the branch.** That's `git push` driven by `/cortex:commit-push-pr`, `/cortex:end`, or the user.
- **Opening a PR.** That's `/cortex:commit-push-pr` (or `gh pr create` by hand).
- **Squashing / rewriting history.** Out of scope — the skill produces forward-only commits.
- **Multi-author / Co-authored-by trailers.** Not added automatically. The user can add them by hand if a real collaborator was involved.
