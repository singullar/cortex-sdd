# Cortex commit conventions — single source of truth

This file is the canonical reference for commit conventions across the Cortex plugin. `cortex:commit`, `cortex:commit-push-pr`, `cortex:exec`, and `cortex:end` all point here so the rules live in exactly one place and never drift.

When a skill instructs "follow the Cortex commit conventions inline," it means: apply everything below verbatim, in-process, without trying to chain into the `cortex:commit` skill via the Skill tool mid-run.

---

## Message shape (no exceptions)

Every commit MUST use this shape — header line + blank line + body:

```
<type>(<scope>): <imperative subject under 72 chars>

<body — 1–4 short paragraphs or bullets explaining WHY this change exists,
what user-visible or design impact it has, and any trade-off or follow-up
the next reader needs to know about. The diff already shows WHAT.>
```

Single-line commits are the failure mode this convention exists to prevent. A header-only commit reads as "trust me"; six months later neither author nor reviewer can tell whether the change was intentional. The body is where intent lives.

## Allowed `type` values (closed set)

`feat` · `fix` · `chore` · `docs` · `refactor` · `test` · `perf` · `ci` · `build`

`feat`, `fix`, `chore` are the primary three and mirror the branch prefixes enforced by `cortex:new-worktree`. The others apply only when they genuinely fit (e.g. `docs:` for README-only changes). The set is closed: no `release`, `style`, `wip` or ad-hoc prefixes, to keep history scannable.

## How to pick `type` from the diff

- **`feat`** — new behavior / new files providing new capability.
- **`fix`** — bug correction, error-path repair, regression fix.
- **`chore`** — config, scripts, plumbing that isn't user-visible (e.g. `.gitignore`, CI tweaks, dependency bumps that aren't security fixes).
- **`docs`** — README/CLAUDE.md/markdown-only changes.
- **`refactor`** — code reorg with no behavior change.
- **`test`** — test-only changes.
- **`perf` / `ci` / `build`** — narrow, obvious cases only.

## How to pick `scope`

Smallest noun that locates the change: a top-level directory (`skills`, `scripts`), a feature area (`worktree`, `auth`), or a specific skill/module (`cortex:exec`). Avoid `misc` / `general` — if you can't name a scope, the commit probably wants splitting.

## Subject line rules

- **Imperative mood.** "add X" / "remove Y" — not "added", not "adds".
- **Lowercase first letter** after the colon: `feat(auth): add JWT refresh path`.
- **No trailing period.**
- **≤72 chars.** If you can't fit it, the commit probably wants splitting.

## Body rules (why this is enforced)

- **Always present, never single-line.** See the failure mode above.
- **Semantic, not mechanical.** Don't restate the diff ("changed line 42 in foo.py"). Explain motivation, the problem it solves, and any constraint that shaped the approach.
- **Bulleted bodies are fine** when there are 3+ distinct points; prose is fine for a single arc. Wrap prose lines at ~72 chars for `git log` readability.
- **Reference upstream context when it exists** — a ticket ID, an EXP/PLAN section, a prior commit hash — but never invent one.

## English only — non-negotiable

Subject and body MUST be English regardless of the conversation language. If the user's described changes are in another language, translate the *intent* into English for the commit — do not paste the user's prompt verbatim. Rationale: commit history is the long-lived audit trail of the cycle; consistent language keeps it greppable and reviewable by anyone on the team.

## Split vs single commit

Scan the changed files for naturally distinct concerns:

- Group **at the file level only** — never split hunks inside a file.
- Split when two clearly unrelated changes are mixed (e.g. a refactor in `auth/` plus a new feature in `billing/`).
- Default to a single commit when in doubt. Two or three logical commits is the sweet spot — over-slicing into many tiny commits creates noise.

## Mechanics

- **Stage explicit files only.** `git add -A` and `git add .` are forbidden — they sweep secrets and unrelated changes. If the change set really is everything, list the files explicitly.
- **Heredoc to preserve newlines** in the message:

  ```bash
  git add path/to/file1 path/to/file2 && git commit -m "$(cat <<'EOF'
  <type>(<scope>): <subject>

  <body paragraph or bullets>
  EOF
  )"
  ```

- **No `--no-verify`.** Hook failures are signal, not noise. Read the output, fix the underlying issue, re-stage, retry as a new commit.
- **No `--amend` on hook-rejected commits.** The commit didn't happen; `--amend` would modify the *previous* commit and may destroy work.
- **Never push from this convention.** Pushing is a separate intent (`/cortex:commit-push-pr`, `/cortex:end`, or user-driven).
- **Never invent ticket numbers, PR numbers, or upstream references.** Cite only what's verifiable from the conversation or repo state.

## Branch guards

- **`main` / `master` / default branch** — warn-and-ask via `AskUserQuestion`: `Continue on <branch>` / `Switch to a new worktree first` / `Cancel`. Reason it's a guard not a refusal: legitimate on-`main` commits exist (the `.gitignore` change `cortex:new-worktree` produces, doc-only tweaks, hotfixes on a single-branch repo). On "Switch to a new worktree first": **halt the skill and print** *"Run `/cortex:new-worktree` to cut the worktree, then re-invoke."* — do NOT chain into `cortex:new-worktree` (the worktree skill is user-invoked).
- **Detached HEAD** → surface it and ask whether to cut a branch first; commits in a detached HEAD are not part of the cycle convention.

## Worked examples

**Example 1 — feature in a skill area:**

```
feat(worktree-skill): scaffold .gitignore on first worktree creation

cortex:new-worktree previously assumed the parent repo already ignored
.worktree/. New users hit this gap and accidentally committed in-progress
trees back to main. Step 4 now appends `.worktree/` to .gitignore (or
creates the file) and reports it back so the user can land the gitignore
change on main as a separate commit.

Idempotent — re-running against a repo that already lists the entry is a
no-op.
```

**Example 2 — bug fix with context:**

```
fix(cortex:exec): respect inline tag when phase Mates lists an owner

The executor previously promoted `_(inline)_` tasks to mate spawns whenever
the surrounding phase carried a `Mates:` annotation. Result: a single
`gh variable list` task burned ~70s of mate cogitation per run.

The inline tag is now binding regardless of the phase header. The owner-tag
inline floor is also unchanged — narrow read-only gates run inline even
when planning over-eagerly assigned an owner.
```

**Example 3 — chore split from a feature commit:**

```
chore(gitignore): ignore .worktree/ scratch directories

Worktrees created by cortex:new-worktree live under `.worktree/<prefix>-<slug>/`
and must never be committed back. Adds the entry once so subsequent skill
invocations stay idempotent.
```
