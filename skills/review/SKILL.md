---
name: review
description: Use when a PR (or a just-executed cycle) should go through the autonomous review loop — phrases like "cortex:review", "review the PR", "run the reviewers", "autonomous review". Ships pending work as a draft PR when needed, runs the repo's test suite in preflight (auto-detected runner; failures enter the review as seeded findings, re-run after every fix batch), fans out specialized reviewers (pr-review-toolkit agents + docs/security/UX/design-system lenses) armed with the cycle's PLAN.md intent, debates findings, auto-fixes everything confirmed (max 2 rounds, then a round the advisor grants or a `Review round` answer; fix-authors in parallel by file footprint), posts one delivery comment, and flips the PR ready for human review. Draft = machine-owned, ready = human-owned; a ready PR gets a read-only review. Resume-friendly via `.cortex-review-receipt.json`.
---

# Skill: cortex:review

Drives the autonomous review loop defined in `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/review/references/review-protocol.md` — that reference is the single source of truth for the state machine, reviewer matrix, debate rules, fix-loop bounds, and delivery format. This skill is the orchestrator shell: resolve inputs, pick the mode, run the protocol, write the receipt.

**Plugin root:** `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}`, written `<PLUGIN_ROOT>` in this skill's references.

The same protocol is driven inline by `cortex:exec`'s wrap-up ("Ship & autonomous review") — this skill exists for standalone invocation: any PR, any repo, no cycle required.

## Modes

| Mode | Trigger | Behavior |
|---|---|---|
| **cycle-tail** | invoked from (or pointed at) a cycle folder with PLAN.md/EXEC.md and a worktree with pending/unpushed work | full loop: ship (commit → push → `gh pr create --draft`) → review → fix → ready |
| **standalone-draft** | `pr:<num>` (or detectable open draft PR for the current branch) | full loop minus PR creation — preflight pushes any unpushed local commits on the head branch first (never review a stale diff): push → review → fix → ready |
| **read-only** | the target PR is already `ready` (non-draft) | fan-out + delivery comment only — no fixes, no commits, no state change (ready = human-owned) |

## Input

Free-text `args`:
- Optional `pr:<num>` — target PR number on the current repo (`-R <owner>/<repo>` inferred from the git remote).
- Optional `folder:<name>` — cycle folder (relative to CWD) holding PLAN.md/EXEC.md for intent context and the receipt.
- Neither → infer: CWD (or its worktree) has an open PR for the current branch → that PR; else if pending/unpushed work exists AND cycle artifacts (PLAN.md/EXEC.md) are present → cycle-tail mode (no artifacts → step 1 refuses with guidance).

## Steps

1. **Resolve target + mode.** Parse args; find the PR (`gh pr view --json number,isDraft,state,url` on the current branch, or `pr:<num>`). No PR + pending work **+ cycle artifacts (PLAN.md/EXEC.md at the cycle folder)** → cycle-tail. Pending work but NO cycle artifacts → refuse with guidance: *"No PR and no cycle artifacts — run from a cycle folder, or open a draft PR first and re-invoke with `pr:<num>`."* PR draft → standalone-draft. PR ready → read-only (announce it: *"PR is ready — human-owned; running read-only review."*). PR closed/merged → abort.

2. **Receipt check (idempotency).** Which receipt this run uses:
   - cycle-tail → `<cycle>/.cortex-review-receipt.json`;
   - standalone with `folder:<cycle>` → that same canonical file when it is absent or names this PR; when it names **another** PR, this is a second PR of the cycle, and the receipt is `<cycle>/.cortex-review-receipts/<owner>__<repo>__<n>.json` (protocol § "Receipt" — never overwrite the canonical one, never an ad-hoc sidecar);
   - standalone without a folder → `.cortex-review-receipt.json` at the CWD.

   Then read its state:
   - `status: "complete"` → tell the user, ask re-run vs abort.
   - `status: "partial"` → resume: reconcile the receipt against live `git log` + `gh pr view` (git/GitHub win), continue from the first unfinished transition.

3. **Run the protocol.** Read `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/review/references/review-protocol.md` and execute it verbatim: preflight → matrix fan-out ‖ test-run probe §6 in the background → dedup (waits for the probe) → debate → fix rounds (cap 2, then the advisor round; one fix-author per disjoint file group, suite re-run per batch) → out-of-scope triage → delivery evidence → delivery comment → ready flip (or stay-draft). Ship (cycle-tail) additionally reads `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit/references/commit-conventions.md` and `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/commit-push-pr/references/pr-conventions.md`.

4. **Write the receipt** after every state transition (location + schema: protocol § "Receipt") — not just at the end.

5. **Print final summary:**
   ```
   ✓ Autonomous review <converged | converged (ready blocked by required checks) | needs human | read-only> on PR #<num>
   • Reviewers: <list> (<F> fallbacks)
   • Tests: <passed (<runner>) | failed: <n> seeded | no runner | no tests | env-blocked | timed out | skipped — markdown-only>
   • Findings: <C> confirmed → fixed · <D> dismissed · <H> deferred to human · <A> answered · <J> judged · <I> issue-candidates
   • Rounds: <N>/2<, round <n> granted by the advisor> · State: <draft | ready>
   • URL: <pr-url>
   → <"Ready for your review." | "Stayed draft — see the delivery comment for what blocks convergence.">
   ```

## Receipt — `.cortex-review-receipt.json`

Location and schema live in `references/review-protocol.md` (§ "Receipt") — the single source of truth both this skill and cortex:exec's inline consumer read. Cycle-tail writes it at the cycle folder; standalone/read-only write it at the CWD. Every finding entry carries the full pinned schema (`severity`/`file`/`line`/`description`/`category`, plus `ds_case` on `design-system` findings) plus `verdict`/`rationale`/`outcome` (and `triage` on out-of-scope findings), so the audit trail can reconstruct what each finding actually said.

## Constraints

- **The protocol reference is authoritative.** This skill never restates matrix/debate/cap rules; read `references/review-protocol.md` and apply. Same for ship: `commit-conventions.md` + `pr-conventions.md`.
- **`pr-review-toolkit` is an optional dependency.** The specialized agentTypes are used when spawnable; the fallback (retry once → `general-purpose` with the same pinned finding schema, recorded in `spawn_fallbacks`) is the contract, not a degraded afterthought. Absence of the plugin never blocks the loop.
- **Ready PRs are human-owned.** Read-only mode never commits, never flips state. The only way the machine re-acquires a ready PR is the human converting it back to draft themselves.
- **Lead-only writes.** Reviewer/defender subagents report findings; fix-authors edit only their group's files and never commit (protocol § "Fix loop"); only the lead commits, pushes, posts comments, writes the receipt and the evidence files.
- **Artifact chain only.** This skill trusts the cycle's artifacts (PLAN.md/EXEC.md when present) and the PR itself. Out-of-scope suggestions are triaged (questions answered, opinions judged, facts reported) and live only in the delivery comment, as non-blocking rows. The loop never files GitHub issues.
- **English only** in the receipt, delivery comment, and subagent reports.
- **No Team Agents, no `Skill(...)` chaining mid-run, no `--no-verify`, no force-push.** All inherited from the protocol; listed here because they are the traps.
- **No silent scope additions.** `issue-candidate` rows in the delivery comment are the pressure valve: they never enter EXEC.md and never widen the PR.

## Out of scope

- **Merging the PR** — human (or CI) decision; `/cortex:end` verifies merged state downstream.
- **Replanning** — a finding that invalidates the plan is `deferred to human`; the human decides whether to re-run `/cortex:plan`.
- **CI/GitHub Actions integration** — the loop is in-session by design.
