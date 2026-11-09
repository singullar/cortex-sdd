---
name: new-worktree
description: Use whenever a Spec-Driven Development cycle is about to make code changes in a target repo and an isolated branch is needed — phrases like "cortex:new-worktree", "create a worktree", "new worktree", "isolate this change", "new branch". Creates `.worktree/<prefix>-<slug>/` rooted at a synced `main`, scaffolds the directory on first use, registers `.worktree/` in `.gitignore`, and enforces the `feat|chore|fix` branch-prefix convention. Every cortex skill that mutates a repo (cortex:plan, cortex:exec) MUST invoke this skill before the first write so changes never land directly on `main`.
---

# Skill: cortex:new-worktree

Creates a fresh git worktree under `.worktree/` so Cortex cycles never edit the working repo's `main` branch directly. Scaffolds the parent dir and `.gitignore` entry on first run — idempotent on subsequent runs.

## Inputs

Collect from the user (or infer from EXP/PLAN context):

- `REPO_DIR`: absolute path to the target repo. Defaults to CWD. Reject `..` or non-git paths.
- `BRANCH_PREFIX`: one of `feat` | `chore` | `fix`. Any other prefix is rejected with a one-line error pointing back to this list.
- `FEATURE_SLUG`: kebab-case descriptor (e.g. `cleanup-stale-tasks`). Auto-normalize spaces/underscores/camelCase to kebab. Reject `/`, `..`, leading/trailing dashes.

If `BRANCH_PREFIX` or `FEATURE_SLUG` is missing and cannot be inferred, ask via `AskUserQuestion` — two questions max:

- **Q1 — header `Prefix`** (singleSelect): `feat`, `chore`, `fix`. Description: "Branch prefix — feat for new functionality, fix for bugs, chore for housekeeping/infra."
- **Q2 — header `Slug`** (Other only — free text): "Short kebab-case descriptor for this branch (e.g. `add-retry-logic`)."

## Derived values

- `BRANCH_NAME = ${BRANCH_PREFIX}/${FEATURE_SLUG}`
- `WORKTREE_DIR = ${REPO_DIR}/.worktree/${BRANCH_PREFIX}-${FEATURE_SLUG}`

## Steps

1. **Verify `REPO_DIR` is a git repo.**

   ```bash
   git -C "${REPO_DIR}" rev-parse --show-toplevel
   ```

   If this fails, abort: *"cortex:new-worktree requires a git repository. <REPO_DIR> is not one."* The user has to point at a real repo.

2. **Reject pre-existing branch/worktree collisions.** Run both:

   ```bash
   git -C "${REPO_DIR}" worktree list
   git -C "${REPO_DIR}" branch --list "${BRANCH_NAME}"
   ```

   If `${WORKTREE_DIR}` already appears, or `${BRANCH_NAME}` already exists, abort: *"Worktree or branch already exists — pick a new slug or `cd` into the existing worktree."* Never silently reuse — that risks stomping on in-flight work.

3. **Scaffold `.worktree/` on first use.**

   ```bash
   mkdir -p "${REPO_DIR}/.worktree"
   ```

4. **Register `.worktree/` in `.gitignore`.** Read `${REPO_DIR}/.gitignore` (create empty if absent). If neither `.worktree` nor `.worktree/` appears as a standalone line (no leading `#`), append `.worktree/` followed by a trailing newline. If the entry is already present, leave the file untouched — never duplicate.

   Why this matters: worktree directories are local-only scratch and must never be committed back. Missing the gitignore step is the single biggest cause of accidental commits of in-progress trees.

5. **Sync `main` before branching.** Run from `${REPO_DIR}`:

   ```bash
   git -C "${REPO_DIR}" fetch --prune origin
   git -C "${REPO_DIR}" checkout main
   git -C "${REPO_DIR}" pull --ff-only origin main
   ```

   If `pull --ff-only` fails (local `main` has diverged), abort with the actual git output. Do NOT attempt `--rebase` or `--no-ff` — the user must reconcile their `main` manually before a worktree is safe to cut.

6. **Create the worktree on a new branch off the freshly-pulled `main`.**

   ```bash
   git -C "${REPO_DIR}" worktree add -b "${BRANCH_NAME}" "${WORKTREE_DIR}" main
   ```

7. **Confirm and surface next-step path.**

   ```bash
   git -C "${REPO_DIR}" worktree list
   ```

   Print to the user:

   ```
   ✓ Worktree ready
   • Branch: ${BRANCH_NAME}
   • Path:   ${WORKTREE_DIR}
   → cd ${WORKTREE_DIR} to start working.
   ```

   If the `.gitignore` was modified in step 4, also surface: *"Note: `.worktree/` added to `${REPO_DIR}/.gitignore` — commit that change separately on `main`."*

## Constraints

- **Branch prefix is a closed set: `feat`, `chore`, `fix`.** No `refactor`, no `release`, no ad-hoc prefixes. Keeps cycle history scannable. If a user pushes back, surface the constraint and let them choose from the three — do not silently widen it.
- **Always branch off freshly-pulled `main`.** A stale `main` is the #1 cause of avoidable merge conflicts in Cortex cycles. Step 5 is non-skippable.
- **Never reuse an existing worktree or branch name.** Collisions abort, not overwrite.
- **`.gitignore` write is idempotent.** Re-running the skill against the same repo MUST NOT duplicate the `.worktree/` line.
- **Only operates on the repo at `REPO_DIR`.** Does not touch sibling repos, even when `.claude/settings.json` lists them under `additionalDirectories` — those need their own invocation.

## Out of scope

- Pushing the new branch to `origin`. The branch stays local until the user (or a downstream skill like `cortex:end`) decides to push.
- Opening a PR. That's `/cortex:commit-push-pr` (or `gh pr create` by hand).
- Cleaning up old worktrees. Merged-PR worktrees are removed at cycle close by `/cortex:end`'s cleanup step; `git worktree remove` remains available manually anytime.
- Working without `main` as the trunk. Repos with `master` / `trunk` / `develop` as the default branch are not supported — extend the skill explicitly when that case arises.
