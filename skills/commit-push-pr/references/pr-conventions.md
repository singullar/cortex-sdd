# Cortex PR conventions — single source of truth

This file is the canonical reference for PR title/body drafting across the Cortex plugin. `cortex:commit-push-pr`, `cortex:exec` (ship step), and `cortex:review` all point here so the rules live in exactly one place and never drift.

When another skill needs to draft a PR, read this file and apply everything below verbatim, in-process, without trying to chain into the `cortex:commit-push-pr` skill via the Skill tool mid-run (no-return-semantics trap).

## Scope of title and body

The title and body cover **all commits in the PR**, not just the most recent diff. Re-read the per-branch git log against base before drafting:

```bash
cd "${REPO_DIR}" && git log --format='%s%n%n%b%n---' "${BASE_BRANCH}..HEAD"
```

## Title rules (same shape as a commit subject)

- `type(scope): subject` matching the dominant commit on the branch.
- English, imperative, ≤72 chars, lowercase after the colon, no trailing period.

## Body structure — semantic, English, multi-line

Use this template:

```markdown
## Summary
<1–3 sentence prose explaining what changes and why it matters.
Focus on the user-visible or design impact, not the diff mechanics.>

## Why
<Bullet or short prose on the motivation: the bug being fixed, the
capability being added, the constraint that prompted this. Cite the
PLAN.md section, ticket, or EXP.md finding when one exists in the
working tree — do NOT invent references.>

## Changes
- <Bullet per logical change, file-level granularity. Mention the
  module/skill/area touched, not every file path.>
- <…>

## Validation
- <How this was verified: the EXEC.md gate that passed, a test name,
  a manual check the reviewer can reproduce, a screenshot link.>

## Risk & rollback
<One short paragraph or bullets: what could break, who owns the
fallback, how to revert (commit hash, feature flag, config).>
```

## Section adaptation

- **Trivial changes** (docs typo, single-line config) → keep `## Summary` + `## Why` only; omit Changes/Validation/Risk if they'd be padding. Empty sections are worse than absent ones.
- **No PLAN.md/EXEC.md present** → that's fine; just don't fabricate citations. The Why section still exists, sourced from the diff and the conversation.
- **Known effects (conditional)** → when the run's `discovered_work` (in memory while exec ships inline, else `.cortex-exec-receipt.json`) holds an optional or out-of-scope addition (`/cortex:exec` § "When evidence contradicts the plan"), add `## Known effects` after `## Risk & rollback`: one line per item, naming it, what it leaves open and any effect a user can see. The operator reads it before the merge. Omit the section when there is none.
- **Existing PR (refresh mode)** → diff the new body against what `gh pr view --json body --jq .body` returns. Tell the user what changed before applying.

## Mechanics

- Pass the body via stdin (`--body-file -` with a heredoc) so newlines and special characters survive shell escaping.
- Add `--draft` to `gh pr create` when the flow calls for a draft PR (user asked for one, or the `/cortex:review` loop is about to run: draft = machine-owned, ready = human-owned).
- On `gh` errors (auth, repo missing, no GitHub remote), surface the error verbatim — do NOT silently retry.
- **Never fabricate references.** Cite PLAN.md/EXEC.md sections, ticket IDs, or commit hashes only when verifiably present. Inventing them is the worst failure mode for a PR body — it survives review and rots silently.

## Worked example

```markdown
## Summary
Adds a worktree-isolation guard so Cortex cycles never modify the source
repo's `main` branch directly. Cycles now branch into
`.worktree/<prefix>-<slug>/` automatically and the parent repo's
`.gitignore` is updated on first use.

## Why
The previous flow let mates write straight to `main`, which broke
rollback (no atomic revert point per cycle) and corrupted the PR-level
audit trail PLAN.md assumes. See PLAN.md § "Change targets".

## Changes
- `skills/new-worktree/` — new skill that scaffolds the worktree
  and registers `.worktree/` in `.gitignore` (idempotent).
- `skills/plan/SKILL.md` — Constraints now require an inline
  worktree task in EXEC.md's `Cross-cutting > Setup`.
- `skills/exec/SKILL.md` — Lead verifies the worktree exists
  before any mate spawn that writes files.

## Validation
- Manual run on a scratch repo: `git worktree list` shows the new entry
  on first invocation; second invocation is a no-op (idempotent path).
- `.gitignore` correctly skipped duplicate append on re-run.
- Re-ran cortex:exec's existing receipt-resume flow against a worktree —
  EXEC.md ticks remained correct (lead is still the single writer).

## Risk & rollback
Low. The new skill is opt-in (only triggers when the user invokes
`/cortex:new-worktree` or when cortex:plan emits the task). Rollback:
revert the three SKILL.md edits; the new skill directory can be left
in place harmlessly. No data migration, no schema, no flag.
```
