# Delivery lifecycle

What "done" means between `/cortex:exec`'s wrap-up and the vault. `/cortex:end` and `/cortex:status` both read this page.

| Fact | Evidence (and only this evidence) | Moved by |
|---|---|---|
| **executed** | exec receipt `status: complete` and EXEC.md fully ticked | `/cortex:exec` |
| **delivered** | review receipt `status: complete`; its `terminal` says which kind of delivery | `/cortex:review` |
| **merged** | every delivered or cited PR is `MERGED`, observed with `gh pr view`, never inferred from disk | the operator |
| **closed** | end receipt with `closed_at` | `/cortex:end`, every gate green |
| **ingested** | end receipt `ingested: true` | `/cortex:ingest` |

**An open PR is delivered but not closed: a checkpoint, never a closure.** Draft or ready, even when the approved scope ends there, `/cortex:end` waits for the merge. Delivery alone never triggers a merge, a ready flip or an ingest, and unmerged-PR evidence stays in the cycle's receipts, out of the vault.

| State | Owner | Next |
|---|---|---|
| `ready`, `read-only`, or no `terminal` | operator | Merge |
| `draft-blocked-by-checks` | operator | Get checks green, flip ready, merge |
| `draft-kept-by-scope` | none | Nothing owed |
| `draft-needs-human` | operator | Settle findings, flip ready, merge |
| Closed unmerged | operator | Reopen or replan |
| Receipt partial or aborted | session | `/cortex:review` |
| All merged, no end receipt | session | `/cortex:end` |

Read each open or closed PR separately. GitHub wins over the recorded `terminal`: an unmerged draft that `terminal` does not explain reads as `draft-needs-human`, and an open PR with no review receipt reads as `Receipt partial or aborted`. With several PRs, Next names the first that applies, in this order: closed unmerged, partial or aborted, `draft-needs-human`, `draft-blocked-by-checks`, merge.
