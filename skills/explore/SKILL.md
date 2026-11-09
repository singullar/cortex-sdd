---
name: explore
description: Use when the user describes a problem or idea and wants to UNDERSTAND it before planning a solution — phrases like "cortex:explore", "explore this", "let's understand this better", "before solving, I want to explore". Produces a lean EXP.md (≤1 page) designed as input for the downstream `cortex:plan` skill. Out of scope — solutions, architecture (that's `cortex:plan`), implementation plans.
---

# Skill: cortex:explore

Produces `EXP.md`: a lean, structured starting point for the downstream `cortex:plan` skill. Not a research dump.

## Contract with cortex:plan

`cortex:plan` consumes `EXP.md` and needs five things to start:

1. The problem framed clearly.
2. Known constraints / facts the plan must respect.
3. Open questions that **block planning** — until resolved, no plan is sound.
4. Scope: what is in, what is out.
5. Pointers to where context lives (so the planner can dig deeper without re-discovering).

That's it. EXP.md exists to deliver those five things. Everything else is exploration noise that belongs in the conversation, not the doc.

**Out of scope — refuse if asked inside this skill:** solutions, architecture proposals, implementation plans, technology picks, partial designs. If the user pushes for solutioning, redirect: *"Close the open questions in EXP.md first. Then: `/cortex:plan`."*

## Input

Free-text `args`. Optional token (recognized at the start of args, stripped before the remainder is treated as the problem description):

- `folder:<name>` — relative-to-CWD subdir for the artifact. Rejected if `..` or absolute.

Examples:
- `Deploys are timing out at 30min, only on Tuesdays. No clear pattern.`
- `folder:tuesday-deploys Deploys are timing out at 30min...`
- (when EXP.md exists) `Add what we just learned: correlates with the weekly Kafka rebalance.` → refines existing doc.

## Steps

> **Critical sequencing rule.** Do the ai-brain lookup **inline by reading `index.md`** (step 5) — a plain `Read`, which returns normally. Do **not** route it through the `Skill` tool or a subagent, from the lead or anywhere else: the Skill tool can't reliably return control mid-flow, and a subagent only adds an async surface the lead has to wait on. Also: write the skeleton EXP.md BEFORE the enrichment step, so any hiccup still leaves a usable doc on disk.

1. **Parse args.** Strip leading `folder:<name>` token if present (reject `..`, absolute paths for `folder`). The remainder is `PROBLEM`. If both `PROBLEM` is empty and no EXP.md exists, ask the user: *"Describe the problem in your own words. If you have a hypothesis or have already ruled something out, mention it — those land in Known. Anything you're unsure about lands in Open questions."* This single open prompt is the only question this skill asks — there is no chip-based probe.

2. **Resolve target.** `TARGET_DIR = ${CWD}/${folder}` (or `${CWD}`). `mkdir -p` if missing. `TARGET = ${TARGET_DIR}/EXP.md`.

3. **Mode.** File missing → fresh (iteration 1). File exists → deepen (read it, increment iteration, refine bullets in place — do not duplicate, do not preserve struck-through history).

   **Peer-session guard (before any write).** A cycle folder must be worked by exactly ONE session: racing sessions clobber `EXP/PLAN/EXEC.md` and can each cut worktrees and PRs. Detect a peer with either signal: (a) `ListAgents` (when the tool is available) shows another live local session on this same cycle folder; (b) deepen-mode only — `TARGET` was modified within the last ~10 minutes and NOT by this session (an mtime this fresh on entry means someone else is mid-explore). On detection: ONE `AskUserQuestion` (header `Peer session`, singleSelect): `Abort — let the peer finish (Recommended)` / `Continue here (take over; I closed the other session)`. No peer signal → continue silently (this guard asks nothing in the normal case).

4. **Write SKELETON EXP.md** using `PROBLEM`, the conversation, and (if present) `${CWD}/CLAUDE.md`. Infer **Scope** (In/Out) from the problem text — leave `TBD` where the text doesn't support a boundary (don't invent one). Pull any **Known** facts that come straight from the description, CLAUDE.md, or the conversation; if the user stated a working hypothesis or something already tried/ruled out, write `Working hypothesis: <text>` and/or `Already tried/ruled out: <text>` to **Known**, and for a hypothesis also seed **Open questions** with `Verify hypothesis by: <…>`. Leave **Context pointers** and the ai-brain-enriched parts of **Known** as `_(pending enrichment)_`. Write the file to disk now — this is the durable safety net.

5. **Enrich inline — read `index.md`, never route through the `Skill` tool or a subagent.** Conditional on ai-brain configuration. Check the current session's CLAUDE.md (already in context): if the Knowledge lookup section lists an `ai-brain vault` path, extract it as `AI_BRAIN_PATH`. If no path is present (Knowledge lookup section absent or path missing), skip this step — replace all `_(pending enrichment)_` placeholders with `_(no ai-brain configured for this session)_` and proceed to Step 6.

   When `AI_BRAIN_PATH` is available, the lead does the lookup itself with plain `Read`: read the vault index, match on titles + one-line descriptions. **No subagent** — it only adds an async surface to wait on.

   a. From `PROBLEM`, pick the non-obvious terms — service names, acronyms, internal entities. **Cap 5** (see constraint).
   b. **Section-scoped index read** — `${AI_BRAIN_PATH}/index.md` is NOT small (grows with every ingest); never `Read` it whole. First `grep -n '^## ' "${AI_BRAIN_PATH}/index.md"` to get the H2 boundaries (e.g. `## Sources`, `## Concepts`, `## Entities`), then `Read` with `offset`/`limit` only the section(s) the terms call for — service names/acronyms/internal entities → **Entities** (and **Concepts** when the term reads like a pattern/methodology); skip **Sources** unless a term is clearly a document title. For each term, scan the loaded section's `[[wiki/...]] — <description>` lines for title/description matches; take the top-ranked hit(s) with their vault paths.
   c. For a matched term, optionally `Read` the frontmatter + first H2 of the top hit to sharpen the one-liner (cap ~5 hit reads total). For an unmatched term, record `not found`.
   d. Findings: `term → 1-line definition + citation (vault path)`, or `term → not found`. No solution language — definitions only.

   If `TARGET_DIR` holds other files obviously relevant to PROBLEM, the lead may `Read` up to 3 of them and fold pointers in too.

6. **Merge enrichment into the skeleton.** Using the step-5 findings: for each term, if defined, add a one-liner to **Known** (cite "_via ai-brain_" + path) or **Context pointers** as appropriate. If "not found", append it as an **Open question** — the gap itself is the signal. Update **Open questions** with anything the lookup surfaced as unclear. Bump iteration if this was a deepen.

7. **Read `${TARGET}`** back into the current turn's context.

8. **Print** one short confirmation:
   ```
   ✓ EXP.md <created|updated, iter N> · <K> open questions blocking planning
   → Top question: <quote question #1>
   ```

## EXP.md template (LEAN — ~5 sections, ~1 page)

```markdown
# EXP — {{SHORT_TITLE}}

> Iteration: **{{N}}**

## Problem
{{2–4 sentences. What is happening, who is affected, why it matters.}}

## Known
- Fact / constraint (cite source if external — e.g. `path/to/file.ts`, `#channel`, ai-brain).
- ...

## Open questions (blocking planning)
Each must include *why* it blocks the plan.

1. **Question.** Why it blocks planning: ...
2. ...

## Scope
- **In:** ...
- **Out:** ...

## Context pointers
Where the planner can go to learn more. Be specific.

- `path/to/file.ext` — what's there
- Dashboard / runbook / Slack channel — what to look for
- ai-brain entries found through the vault's `index.md` — concept name → key takeaway
```

## Constraints

- **English only in EXP.md.** Every section body, bullet, and table cell is in English — regardless of the user's conversational language. The artifact is consumed by downstream skills and indexed in ai-brain; consistent language matters.
- **The advisor is not a step of explore.** Call it only when a decisive fact may be missing and the `advisor` tool is available, with the explore agenda of `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/exec/references/advisor-agenda.md`: "which decisive fact is missing, and who answers it now?", "is this the minimum scope?", and "what does the problem need that the scope leaves out?". Each decisive fact it names becomes an open question. When no such fact is missing, or the tool is unavailable, make no call.
- **Lean over complete.** If a section is bloating past ~6 bullets, the skill is over-collecting — trim or move to Context pointers. **Budget: EXP.md ≤ ~4 KB** (about one page). Check `wc -c` after each write. Over the budget → trim, or move the detail to Context pointers, before the confirmation print.
- **No solution language.** Phrases like "we should", "the fix is", "let's switch to X" do not appear in EXP.md. If the user provided one, transform it into an Open question: *"Would `<X>` address this? Verify by ..."*.
- **No meta sections.** Don't add "Risks of proceeding", "Framings", "Alternate angles" — those are exploration *thinking*, not plan input.
- **Deepen, don't duplicate.** Re-running merges/refines bullets in place. No strike-through history, no append-only logs. The doc is a snapshot of current best understanding.
- **Cap ai-brain lookups at 5 terms per run.** Terms skipped get listed as Open questions (the gap is itself a signal).
- **Brain enrichment is conditional on ai-brain configuration.** If the session's CLAUDE.md has no `ai-brain vault` path in the Knowledge lookup section, skip Step 5 entirely and mark enrichment fields as `_(no ai-brain configured for this session)_`. Never attempt to guess a vault path.
- **The lookup is inline, never via a subagent or the `Skill` tool.** Read `${AI_BRAIN_PATH}/index.md` directly (section-scoped — grep the H2s, `Read` only the needed section). The Skill tool can't reliably return control mid-flow and can leave the EXP.md write unreached; a subagent only re-adds that async surface. A plain inline `Read` always returns. Never `Read` the whole index (it grows with every ingest).
- `TARGET_DIR` must be inside or equal to CWD. Never write outside.
- One file only: `EXP.md`. No sibling docs.
