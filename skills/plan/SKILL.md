---
name: plan
description: Use when the user wants to turn an explored problem into an actionable plan — phrases like "cortex:plan", "let's plan this", "build the plan", "now the plan". Reads `EXP.md` from the target folder (or accepts a freeform text description if no EXP.md exists) and produces two files — `PLAN.md` (the plan) and `EXEC.md` (the executable TODO list with validation gates; subagent-owner execution is the default so the lead's context stays lean, structured into parallelizable waves — one `_(owner)_` per independent workstream — whenever the work allows). Spawns inline subagents in parallel for verification and risk analysis. Advisor-reviewed before final output when the `advisor` tool is available.
---

# Skill: cortex:plan

Turns exploration (EXP.md) into an actionable plan. Produces two files at the target folder:

- **`PLAN.md`** — the plan: objective, approach, verified facts, risks, scope.
- **`EXEC.md`** — the executable TODO list, broken into phases/tasks with **validation gates**.

## Contract — what consumes what

- **Input**: `EXP.md` produced by `/cortex:explore` (preferred), OR freeform text description if no EXP.md exists.
- **Output**: `PLAN.md` + `EXEC.md`, both in the target folder. EXEC.md is what the user (or a downstream executor) checks off as work progresses.

## Roles — lead + subagents (NOT a team)

Planning is one-shot parallel verification. It does NOT need Team Agents (`TeamCreate` / shared TaskList / inter-mate `SendMessage` / idle notifications). Plain `Agent(...)` calls in parallel are simpler, cheaper, and have proper return semantics. **Team Agents are used nowhere in the cycle** — the downstream `cortex:exec` also drives EXEC.md with plain inline `Agent(...)` (see "Execution requirements" below).

- **Main context = lead.** Runs on the session model. Owns synthesis, decisions, the advisor pass, and the final files.
- **Subagents = scoped workers.** Spawned via plain `Agent(description, subagent_type, prompt)` with `model: "sonnet"`. Each has a single responsibility (verify code, analyze risk, design gates) and returns a compact report. They run in parallel via a single message with multiple tool calls. The role rides in `description:`, **never `name:`** — a named spawn becomes an in-process teammate that hangs the lead in teams-enabled sessions. NO `team_name`, NO `TeamCreate` — they are standalone.
- The lead reviews each subagent's output critically before incorporating it. Unverified claims become open caveats, not facts.

## Input

Free-text `args`:
- Optional `folder:<name>` prefix (relative to CWD).
- The rest is a freeform description, used **only** if no `EXP.md` exists at the target.

Examples:
- (no args, EXP.md in CWD) → reads `./EXP.md`, plans from there.
- `folder:tuesday-deploys` → reads `./tuesday-deploys/EXP.md`.
- `Plan the migration of service X to the new auth model` → no EXP.md needed; plans from this text alone (but warn the user that planning without exploration is brittle).

## Steps

1. **Parse args.** Strip leading `folder:<name>` (reject `..`, absolute paths). Remainder is `FREETEXT` (may be empty).

   **Then immediately call `EnterPlanMode()`.** This skill produces an implementation plan — Plan Mode is the right harness for it. Entering it early signals to the user that you're planning (not executing), and ExitPlanMode at the end gives them an explicit approval gate before any downstream skill can act on PLAN.md/EXEC.md. Skipping `EnterPlanMode` is a bug.

2. **Resolve target.** `TARGET_DIR = ${CWD}/${folder}` (or `${CWD}`). `mkdir -p` if missing. `EXP = ${TARGET_DIR}/EXP.md`, `PLAN = ${TARGET_DIR}/PLAN.md`, `EXEC = ${TARGET_DIR}/EXEC.md`.

3. **Resolve input.**
   - `EXP.md` exists → primary input. If `FREETEXT` is also non-empty, treat it as a supplemental hint; surface to the user that EXP.md is the source of truth.
   - `EXP.md` missing + `FREETEXT` non-empty → plan from text. Warn: *"Planning without exploration is brittle — consider `/cortex:explore` first."*
   - Both missing → ask the user for input. Do not invent.

4. **Read session context.** `${CWD}/CLAUDE.md` if present. Also read `${TARGET_DIR}/CLAUDE.md` if different.

5. **Decide which subagents to spawn — DEFAULT IS NONE.** The three subagents below are a *menu*, not a checklist. Spawn each ONLY if the trigger condition holds. For small / well-scoped plans, the lead does the work inline and zero subagents are spawned — that's the right answer, not a shortcut.

   **Spawn-or-not heuristic** (apply per candidate subagent):
   - Would executing this in main context burn **>10–15k tokens** of file reads or analysis the lead won't otherwise use? Then offload.
   - Is the work clearly parallelizable with another candidate? Two parallel offloads beat one main-context serial pass.
   - Can the lead do it in ≤3 tool calls right here? Then do it inline; don't spawn.
   - If undecided after the above, **lean toward inline** — over-spawning is the failure mode to avoid.

   **Subagent menu** (use `model: "sonnet"`, semantic role in `description:` — never `name:`, no `team_name`, no `TeamCreate`):
   - `repo-scout` — spawn when EXP.md cites ≥3 files/repos that need verification. Skip if the lead can `Read` 1–2 files directly. Prompt: *"Verify these claims from EXP.md by reading the actual code/config in <repos>: <list>. Report VERIFIED / CONTRADICTED / MISSING with file:line citations. Do not propose changes."* `subagent_type: "Explore"`.
   - `risk-analyst` — spawn when EXP.md has ≥4 open questions OR ≥4 assumptions to cross-check. Skip if the risk landscape is small (1–2 obvious items the lead can think through). Prompt: *"For each open question + assumption in EXP.md, list what breaks if it's wrong. Also list edge cases the plan must handle. Return a risk table."* `subagent_type: "general-purpose"`.
   - `gate-designer` — spawn when the plan area is unfamiliar (uncommon tools, new infra) and gate design requires research. Skip if gates are obvious (`gh pr view`, `terraform plan`, `kubectl get` — the lead picks these in one read). Prompt: *"For each likely task in this plan area, propose a concrete validation gate — a command, query, or test. Aim for objective checks."* `subagent_type: "general-purpose"`.

   **Rules when spawning:**
   - If spawning ≥2, run them concurrently (parallel tool calls in one message).
   - **Verify, don't assume.** If a subagent cannot find evidence, it MUST report `MISSING` — never fabricate. The lead drops any unverified claim into PLAN.md as an open caveat, not as a fact.
   - **Report which subagents you spawned (and why) in the macro summary's confirmation block** — so the user can see "Subagents used: none" or "Subagents used: repo-scout (5 file claims to verify)" and calibrate. The empty case is a signal of effective scope, not a failure.

6. **Resolve blocking open questions BEFORE writing PLAN.md.** Aggregate every open question from EXP.md and from the subagents' reports. For each, classify:
   - **Blocking** — the plan's shape changes depending on the answer (e.g., "does service X already emit metric Y?", "which target region?"). The plan is *not sound* without this.
   - **Non-blocking caveat** — plan works either way; just needs to be acknowledged (e.g., "vendor's roadmap may shift Q3"). These go into PLAN.md's "Caveats & assumptions" section.

   For blocking questions: **call `AskUserQuestion`** with 1–4 of them per call (the tool's max). Phrase each as a real decision the user can make, with concrete options when possible. If you have more than 4 blocking questions, batch by topic and ask in rounds — do not write the plan with blockers unresolved.

   - Each answered question becomes a **known fact** in PLAN.md (cite as `(resolved by user)`).
   - If the user explicitly defers a question ("skip", "I don't know yet"), demote it to a caveat with an explicit assumption: *"Proceeding under the assumption `<X>` — verify before execution."*
   - **A material divergence is never demoted to a caveat:** a subagent `CONTRADICTED`/`MISSING` finding that changes the Objective, Scope, an acceptance criterion or a resolved answer is a blocking question, asked here as one of this step's questions. Technical adjustments within the intent stay ordinary verified facts.
   - **An apply or rollout the cycle runs is a plan-time decision.** When Scope.In includes applying IaC, migrating a live environment, or a rollout or deploy step the cycle itself runs, PLAN.md settles the apply path before exec starts:
     - the plan's expected delta (added, changed, destroyed, and any replacement);
     - which image or revision;
     - the rollback.

     When verified facts settle it, record it in Approach with its provenance. When the delta cannot be verified at plan time (no plan-time access, or it depends on IaC that Phase 1 writes), state the **intended** delta and give exec a gate that compares the real plan output against it before any apply. When two paths stay viable, it is a material divergence: asked here, never an `Assumption:`. It is never left for exec to discover.
   - Never write PLAN.md with an "Open questions still unresolved" section. By the time the file is written, every question is either resolved, demoted to a caveat with a stated assumption, or the plan is paused.

7. **Synthesize PLAN.md** from EXP.md + subagent outputs + user-resolved answers. Use the template below. Cite verifications by subagent role (`(verified by repo-scout — path/to/file.ts:42)`) and resolutions by source (`(resolved by user)`, `(assumed pending verification)`).

   **Budget: PLAN.md ≤ ~10 KB.** Check `wc -c` after writing. Over the budget → move task-level detail to EXEC.md and background to Verified facts' citations. Never drop an acceptance criterion, a risk row, a decision or a verified fact to fit.

8. **Synthesize EXEC.md** — break the plan into a phased TODO list. Each task carries a **validation gate** wherever a concrete check is possible. If a gate is impossible, mark it `_(no automatic gate — manual review)_` rather than skipping.

   **Trace every phase back to PLAN.md.** Each EXEC phase must map to a workstream from PLAN's "Approach" OR to an item in PLAN's "Scope > In". A PLAN WS may legitimately split into multiple EXEC phases (e.g., WS4 "GitHub side + cold E2E + cleanup" → Phase 4 GitHub side, Phase 5 E2E, Phase 6 cleanup) — that's fine. What is NOT fine: an EXEC phase whose work is not declared anywhere in PLAN's Approach or Scope.In. If you find yourself wanting to add a phase like "observability" / "monitoring" / "docs cleanup" that the PLAN didn't anticipate, you have two choices — never silently add it:
   - **Update PLAN.md** to include the new scope (add to Approach or Scope.In, re-run the advisor pass when available), then add the EXEC phase.
   - **Drop the phase** from EXEC if it's out of scope for this plan.

   Same rule for "Cross-cutting" tasks: only include items that PLAN explicitly mentions (e.g., the docs-update task tied to PLAN's "Docs flagged for update" section, or the final review gate — ship-and-autonomous-review for repo-touching cycles, advisor otherwise; see the wrap-up note in the EXEC.md template).

   **Structure for parallelism — waves.** After tracing phases, run a dependency pass over the authoring tasks: for each pair, ask *"does B need A's output (schema, artifact, merged file) to start?"* Independent authoring workstreams belong in the **same** phase, each as its own `_(owner: <mate>)_` task with a distinct role-named mate, and the phase's `Mates:` annotation marked `_(parallelizable)_` — at exec time that becomes one subagent per owner, spawned concurrently in a single message (e.g. a "Build" wave: `db-author` write-model schema + migration ‖ `worker-author` queue consumer ‖ `iac-author` service stack). Only a true dependency justifies pushing work into a later phase; "to be safe" is not a dependency. A dense plan that comes out as a serial chain of single-task phases is the **under-parallelization anti-pattern** — the mirror image of over-spawning trivial gates. Dense plans should normally contain at least one wave; if none is possible, the dependency chain that forces full serialization should be visible in PLAN.md's Approach. And serialization constrains ORDER, not execution locus — a fully serial plan still offloads: each phase's coupled tasks become one solo-owner batch (see the tagging discipline), and the lead executes only floor tasks inline.

   **Profile: small.** Mark `> Profile: small` in PLAN.md's header only when **every** criterion below holds and each cites its fact (a file read, a grep, the scope). Otherwise mark `> Profile: standard`. The criteria:
   - no migration or schema change;
   - no transaction, lock or concurrency control;
   - no financial or limit rule;
   - no authn/authz change;
   - an estimated diff of at most ~150 changed lines **outside test files**: production code, UI and docs alike (the review protocol's file classes, preflight §4).

   Tenant isolation and data integrity beyond migrations are not criteria of their own. A small change touching queries or persistence still gets `security-reviewer` through its fail-closed gate.

   A small plan:
   - has **one** exec phase, so there is no per-phase handoff;
   - has its review run the small matrix (`review-protocol.md` § "Reviewer matrix").

   A small plan keeps step 10's advisor pass and every gate. Exec re-checks the criteria before shipping (`skills/exec/SKILL.md` § "Ship & autonomous review"), and the review re-checks them against the whole PR diff. A criterion found false, at either point, runs the cycle as standard from there: the review runs the full matrix on the whole diff. A scope addition after the plan is approved goes through `/cortex:plan`. A small cycle's re-review then covers the delta since the previously reviewed head, unless the whole diff no longer meets the criteria: then it reviews the whole diff with the standard matrix.

   **Re-run preserves progress.** If `EXEC.md` already exists, read it first and capture every task currently marked `- [x]`. After generating the new EXEC.md:
   - Same-titled tasks that were `[x]` stay `[x]` in the new file.
   - Tasks that no longer exist in the new plan but were `[x]` are moved into a final section `## Removed by replan (was completed)` so the audit trail is preserved.
   - New tasks land `- [ ]` regardless of what was there before.
   - Surface the diff in the final confirmation line (preserved / added / removed).

9. **Scan for docs to update.** Glob `${TARGET_DIR}` and the enclosing repo root (if different) for: `README.md`, `docs/**/*.md`, `CHANGELOG.md`, `ADR-*.md`, and any file under `.memory-bank/`. List candidates that mention the area touched by the plan. **Ask the user**: *"Any of these need updating to reflect the plan? [list]"*. Do **not** modify docs automatically — only collect the answer and surface it as a task in EXEC.md.

10. **Advisor pass, when available.** If the `advisor` tool is unavailable, skip this step and add one line to PLAN.md's Caveats: `advisor unavailable: not reviewed`. Never block or ask because it is missing. Otherwise, before printing the final summary, write the plan agenda of `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/exec/references/advisor-agenda.md` and call `advisor()` for review. The agenda covers both sides: the smallest plan that meets the criteria and what to cut, and which acceptance criterion the plan does not reach. It also asks where the cycle can stall, and has each risk sized as likelihood × impact. **Blocking means only one of three things:** an unmet acceptance criterion, an untreated high risk, or a contradiction with a recorded decision. "Could be more robust" is a note in Caveats, never a re-pass. If advisor flags blocking issues, fix them and re-pass once. Do not loop more than twice.

11. **`ExitPlanMode()` — user approval gate.** PLAN.md and EXEC.md are now on disk; the advisor pass has run or is recorded as unavailable. Call `ExitPlanMode` with no arguments — the harness shows the user the plan file for explicit approve/reject. Do **not** use `AskUserQuestion` for "is this plan okay?" — `ExitPlanMode` is exactly that. The skill considers itself done once the user has acted; any rework triggered by rejection is a new `/cortex:plan` invocation (re-run preserves EXEC.md `[x]` state per step 8). Subagents from step 5 have already returned and exited — no teardown needed.

12. **Print confirmation + macro summary** after the user exits plan mode. Two blocks, clearly separated. The macro summary gives a scan-friendly view of the plan itself (what was decided), independent of the operational status:

    ```
    ✓ PLAN.md written at <abs-path>
    ✓ EXEC.md written at <abs-path> · <N> tasks across <M> phases · <K> gates
    • Subagents used: <none | name (reason), ...>  (none is a valid, often-correct outcome)
    • Docs to update: <count or "none flagged">
    • Advisor: <pass | adjusted after review | unavailable: not reviewed>

    ── Plan summary ──────────────────────────────

    Objective: <one sentence — quoted from PLAN.md's Objective, condensed if >25 words>

    Key decisions (max 5):
      • <decision 1 — concise, 1 line each>
      • <decision 2>
      • ...

    Approach:
      Phase 1 — <name> (<N> tasks)
      Phase 2 — <name> (<N> tasks · wave: <K> owners)
      ...
    Checkpoints: /cortex:exec writes a handoff at every phase close (<M> phases)   (only when M ≥ 2)

    Top risks (max 2):
      • <risk 1 — name + impact tier>
      • <risk 2>

    Out of scope: <comma-separated, ≤1 line>

    → Next: /cortex:exec to execute, or edit EXEC.md first.
    ```

    **Rules for the summary block:**
    - Sourcing per line — be explicit, the summary is a projection of upstream content, not new prose:
      - **Objective**: first paragraph of PLAN.md's `## Objective` section, condensed to ≤25 words.
      - **Key decisions**: synthesized from (a) every `AskUserQuestion` answer resolved in step 6 — those literally ARE the decisions made this cycle — plus (b) the workstream/sequencing choices listed in PLAN.md's `## Approach`. NOT verified facts (those are findings, not decisions).
      - **Approach (Phase N — name (N tasks))**: drawn from EXEC.md's `## Phase N — <name>` headings + count of `- [ ]` tasks under each. Append `· wave: <K> owners` for phases whose `Mates:` annotation is `parallelizable` with K>1 owners — the user should see at a glance where the plan fans out.
      - **Checkpoints**: one fixed line, printed only when EXEC.md has ≥2 `## Phase N` headings — passive visibility that the executor's phase-close checkpoints (skills/exec/SKILL.md § "Phase close") will run; never a question.
      - **Top risks**: the highest-impact rows in PLAN.md's `## Risks & mitigations` table — pick by `Impact` column (high > med > low), break ties by `Likelihood`.
      - **Out of scope**: from PLAN.md's `## Scope > Out` line, comma-joined.
    - Hard caps: 5 decisions, 2 risks, 1 sentence per item. If you're tempted to write more, the summary has become a re-statement of the plan; cut harder.
    - Skip a section if it has nothing real (e.g., "Top risks" omitted if PLAN.md's Risks table is empty; "Key decisions" omitted if step 6 resolved zero blocking questions AND Approach lists only one workstream).
    - No prose paragraphs, no rationale, no citations — those live in PLAN.md. The console summary is for a 5-second scan.

## PLAN.md template

```markdown
# PLAN — {{TITLE}}

> Source: {{EXP.md | inline description}} · Iteration: **{{N}}**
> Profile: {{standard | small — no migration (<fact>) · no transaction/lock (<fact>) · no financial/limit rule (<fact>) · no authn/authz (<fact>) · ~<n> non-test lines}}

## Objective
One paragraph. What this plan achieves. Tie back to EXP.md's Problem statement.

## Approach
High-level strategy. Sequence phases or workstreams. No task-level detail (that lives in EXEC.md).

## Verified facts
Things confirmed by subagents against the actual code/config, or resolved by the user during planning.

- Fact _(verified by `<subagent-role>` — `path/file.ext:line`)_
- Fact _(resolved by user)_
- ...

## Caveats & assumptions
Non-blocking items: the plan works as written, but these need acknowledgement. Each must state the assumption being made and how/when to verify.

- Assumption: `<X>` _(verify before <step Y> in EXEC.md)_

> **Never** include an "Open questions still unresolved" section here. Blocking questions are resolved via `AskUserQuestion` BEFORE this file is written. If something genuinely cannot be resolved, it becomes an explicit assumption above, not a dangling question.

## Risks & mitigations
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ... | low / med / high | low / med / high | concrete action |

## Scope
- **In:** ...
- **Out:** ...

## Dependencies
External requirements: sign-offs, infra, other teams.

## Access
_(only when exec needs external access; omit otherwise)_ What exec needs, each with the probe exec runs before Phase 1 and on every resume: an identity or status call judged by exit status alone, never one that prints a credential, takes a lock or opens a process (`skills/exec/SKILL.md` step 3b).
- `<AWS profile / SSO session · tunnel · console · CLI login>` — probe: `<identity/status command, e.g. aws sts get-caller-identity --profile <p>>`

## Docs flagged for update
- `path/to/doc.md` — why it might need updating
- _(user confirms in conversation; tracked as a task in EXEC.md)_

## Handoff
Execution checklist lives in `EXEC.md` — same folder.
```

## EXEC.md template

> **Tagging discipline — read this before filling the template.** Two task tags drive the executor's spawn decision. Tag every task with exactly one:
> - `_(inline)_` — the lead does the work in main context, no mate spawn. **Reserved for the trivial floor ONLY**: tasks whose gate is a single read-only tool call (e.g., `gh pr view ...`, `gh variable list`, `aws ... describe-*`, `kubectl get ...`, `terraform plan`, a regex grep) — the *gate IS the work* — plus lead-exclusive duties (worktree setup, commits, ship & review, user confirmations). Spawning a mate to run one `gh` command and report back is the over-spawn anti-pattern.
> - `_(owner: <mate>)_` — assign to a mate. **Default for every task above the floor, even in fully serial phases.** The primary rationale is **context offload**, not parallelism: the subagent burns its own disposable window on file reads, tool output, and test iterations, and returns a compact report — the lead's window stays lean for judgment, gates, ticks, commits, and conversation continuity, postponing mid-cycle compacts. Parallelism (the wave rule below) is the bonus case, not the criterion. The `Mates:` annotation per phase lists which mates exist.
>
> **The two tags carry symmetric mandates.** `_(inline)_` guards against over-spawn (a subagent for one `gh` command); `_(owner)_` guards against context burn and under-parallelization: any **authoring or heavy-analysis task** — writing/editing code, IaC, schemas, tests, docs; auditing or debugging across files — MUST carry an `_(owner: <mate>)_` tag. Leaving such work inline bloats the lead's context and (when independent) serializes work the executor could fan out. Both misfires are anti-patterns; neither tag is "safer" than the other.
>
> **Model per owner — cost-effective tiering.** Every `Mates:` entry carries the model tier the executor pins on that owner's spawn: `` `mate` (opus) `` or `` `mate` (sonnet) ``. Assign **opus** when the batch demands deep reasoning or design judgment — architecture/design decisions, frontend/UI/UX authoring, security-sensitive logic, cross-cutting refactors, ambiguous debugging. Assign **sonnet** for well-scoped implementation — code following existing patterns, IaC, config, migrations, tests, docs, scripts. Undecided → sonnet; opus must be earned by the batch's content, not by prestige. The tier rides the owner because execution batches one spawn per owner per phase — so the tier is per-spawn by construction. An owner listed without a tier inherits the session model (not a valid shape for new plans).
>
> **Wave rule.** When a phase holds ≥2 `_(owner)_` tasks with no data/file dependency between them, give each its OWN role-named mate (`db-author`, `worker-author`, `iac-author`, …), each annotated with its model tier, and mark the phase's `Mates:` annotation `_(parallelizable)_` — the executor spawns one subagent per owner in a single message and they run concurrently. Owners in the same wave MUST have **disjoint file footprints**: no two owners write the same file (the lead is the single committer, but concurrent edits to one file still race). Dependent tasks either share one owner (batched in order inside that subagent) or move to a later phase.
>
> **Solo-owner serial phase — a normal shape.** When a phase's tasks depend on each other (shared mental model, B consumes A's output), they go to ONE owner as a single ordered batch — not one spawn per task (a fresh subagent per coupled task re-derives context and diverges), and not inline (that's exactly the context burn the tag exists to prevent). A dense EXEC.md with zero waves but every phase owner-batched is a perfectly good outcome when the dependency chain is real.
>
> **Test-gated tasks carry their loop.** When a task's gate is a test/build/lint run, write the task so the owner runs the whole iterate-until-green loop ("run the gate, fix, re-run; return when green or stuck — never spin") — relaying each failure through the lead defeats the offload. The executor's self-terminating prompt rule bounds the loop.
>
> If most tasks in a phase are `_(inline)_`, the `Mates:` annotation can be omitted for that phase. The lead just rolls through them — but a phase like that should be rare and short; if it holds real work, the tagging is wrong.

```markdown
# EXEC — {{TITLE}}

> Companion to PLAN.md · Iteration: **{{N}}**

## Execution model

- **Inline subagents only — no Team Agents.** Workers are plain `Agent(description, subagent_type, prompt)` calls that run inline under the lead and return synchronously; they spawn only when a task needs one.
- The lead runs on the session model. Each owner-spawn is pinned to the model tier annotated on its `Mates:` entry — `(opus)` for reasoning-heavy batches (architecture, design, frontend/UX, security-sensitive logic), `(sonnet)` for well-scoped implementation. An unannotated owner inherits the session model. No `team_name`.
- Tagging:
  - `_(inline)_` → lead executes in main context, ticks.
  - `_(owner: <mate>)_` → batch-spawn a subagent (role in `description:`, never `name:`) with this task (and other tasks for the same owner in the same phase) per cortex:exec's coordination model.

Tick boxes as work progresses. Each `Gate:` line is a check that must pass before moving on.

## Cross-cutting > Setup
> Only emitted when PLAN.md's "Change targets" names a repo (see the worktree constraint). Omit the whole section otherwise — there is no team to set up.
- [ ] Cut a worktree via /cortex:new-worktree (prefix: feat|fix|chore, slug: {{SLUG}}) _(inline)_
  - **Gate:** `git -C <repo> worktree list` shows `.worktree/<prefix>-{{SLUG}}/` present.

## Phase 1 — {{name}}
> **Mates:** `mate-a` (opus) _(serial — solo owner: one batch spawn carries all its tasks in order)_ — only listed if at least one task in this phase has an `_(owner)_` tag; omit otherwise.

- [ ] Trivial read-only check (the gate IS the work) _(inline)_
  - **Gate:** `gh variable list -R <org>/<repo> --json name | jq -r '.[].name' | grep -q '^FOO_VAR$' && echo present || echo absent`
- [ ] Multi-step analysis or write-heavy task _(owner: `mate-a`)_
  - **Gate:** how to verify (command, query, test, manual review)
- [ ] Dependent follow-up task consuming the previous one's output _(owner: `mate-a`)_
  - **Gate:** test suite green — owner iterates run → fix → re-run until it passes, then returns

## Phase 2 — {{name}} (wave)
> **Mates:** `db-author` (sonnet), `worker-author` (sonnet), `iac-author` (sonnet) _(parallelizable — independent workstreams, disjoint file footprints; executor spawns one subagent per owner in a single message)_

- [ ] Write-model schema + migration _(owner: `db-author`)_
  - **Gate:** migration applies cleanly / schema tests green
- [ ] Queue consumer + handler pipeline _(owner: `worker-author`)_
  - **Gate:** handler unit tests pass
- [ ] Service stack + queue grant (IaC) _(owner: `iac-author`)_
  - **Gate:** `terraform plan` clean — only the expected resources

## Phase 3 — {{name}}

- [ ] Single tool call task _(inline)_
  - **Gate:** ...

## Cross-cutting > Wrap up
- [ ] Confirm with user which "Docs flagged for update" entries in PLAN.md to action _(inline)_
  - **Gate:** user answer recorded; resulting doc-edit tasks added to a phase or executed inline.

> **Final review gate — emit exactly ONE of the two tasks below.** When PLAN.md's "Change targets" names a repo (the cycle mutates code), emit the ship-and-review task: the autonomous review loop replaces the advisor gate (advisor joins the loop's fan-out as a voice when reachable). Only when NO repo is touched, emit the classic advisor task.

- [ ] Ship & autonomous review: commit → push → draft PR → review loop → ready _(inline)_
  - **Gate:** the lead drives `${CORTEX_DIR:-${CLAUDE_PLUGIN_ROOT}}/skills/review/references/review-protocol.md` verbatim to one of its three terminal states — `ready`, `draft + "needs human"` comment, or `draft + "converged (ready blocked by required checks)"` comment; `.cortex-review-receipt.json` written.
- [ ] Final advisor review on the implementation _(inline)_
  - **Gate:** `advisor()` returns no blocking findings; when the tool is unavailable, record `advisor unavailable: not reviewed` in PLAN.md's Caveats and tick the task. _(Emit this task ONLY for cycles that touch no repo — see the note above.)_
```

## Constraints

- **English only in PLAN.md and EXEC.md.** Every section body, bullet, table cell, and gate description is in English — regardless of the user's conversational language. Subagent prompts should also instruct mates to return reports in English so the synthesis stays consistent.
- **No fabrication.** A fact lands in PLAN.md only if a subagent (or the main context) cites a file/line/source. Otherwise it's resolved with the user (`AskUserQuestion`) or written as an explicit assumption.
- **External state must be verified, never assumed.** PR status, merge state, branch state, deploy state, infrastructure state, package versions, ticket status — anything that lives outside the local files MUST be checked with the actual tool before landing in PLAN.md as a fact. Required verifications per state type:
  - **GitHub PR**: `gh pr view <num> -R <owner/repo> --json state,mergedAt,closedAt` — the subagent must quote the actual JSON output. Never assume "needs to be merged" or "already merged" from prose alone.
  - **Git branch / commit**: `git log`, `git show`, `git ls-remote` — quote the output.
  - **Cloud resource existence**: `aws ... describe-* / get-*`, `kubectl get`, equivalent. Quote the tool output.
  - **File presence/content**: `Read`/`Grep` cited by `path:line`.
  - If verification is impossible in this run (auth, network, tool unavailable), the claim becomes an **assumption** in Caveats, NOT a fact. State it: *"Assuming PR #X is merged — verify via `gh pr view ...` before Phase Y."*
- **No dangling open questions in PLAN.md.** If you're tempted to write "we don't know yet" inside PLAN.md, that's the trigger to call `AskUserQuestion` instead. The plan ships either resolved or with an explicit stated assumption — never as "TBD".
- **Parallel subagents only.** Sequential subagent calls are a smell — if you genuinely need stage A's output before stage B, document why; otherwise spawn them concurrently in a single message.
- **Wave-first EXEC structure.** Independent multi-file authoring workstreams land in the SAME phase under distinct `_(owner)_` mates with a `parallelizable` annotation — never serialized across phases "to be safe". Only a real dependency (B consumes A's output) or a shared file footprint justifies sequencing. Serializing independent authoring work is the under-parallelization anti-pattern, symmetric to over-spawning trivial gates (see the wave rule in the tagging discipline).
- **Owner-first execution (context offload).** `_(inline)_` is reserved for the trivial-gate floor and lead-exclusive duties; every other task carries `_(owner)_` even in fully serial phases — the subagent's window is disposable, the lead's is not. Total token spend rises (self-contained prompts, re-reads); lead-context spend falls and mid-cycle compacts get postponed — the intended trade for dense cycles. The executor's inline floor stays as the symmetric guard against trivial spawns.
- **Sonnet for planning scouts, session tier for the lead.** `model: "sonnet"` on every scout THIS skill spawns (step 5's menu) unless the task clearly needs a stronger tier and you state why. The lead's tier comes from the session — do not try to switch it from inside the skill.
- **Model tier per execution owner — sonnet by default, opus where reasoning is the differentiator.** Every owner in EXEC.md's `Mates:` annotations carries an explicit tier — `(opus)` or `(sonnet)` — that cortex:exec pins on the owner's spawn. Opus: batches dominated by deep reasoning or design judgment — architecture/design decisions, frontend/UI/UX authoring, security-sensitive logic, cross-cutting refactors, ambiguous debugging. Sonnet: well-scoped implementation — code following existing patterns, IaC, config, migrations, tests, docs, scripts. Undecided → sonnet. This is cost-effectiveness engineering, not a quality downgrade — every task's gate still validates on real tool output regardless of tier.
- **Role label in `description:`, never `name:`.** Spawn subagents *unnamed* — pass the role as `Agent(description: …)` (`repo-scout`, `risk-analyst`, `gate-designer`), never generic (`agent1`, `worker`). Passing `name:` turns the spawn into an in-process teammate that never returns one-shot (it hangs the lead in any teams-enabled session) and is invisible to `TaskList`/`TaskStop`; an unnamed spawn is a background task that returns and stays correlatable — its `description:` is echoed in the completion summary. The `<mate>` / `_(owner: <mate>)_` tokens in EXEC.md are planning labels, not spawn names.
- **No Team Agents anywhere in the cycle.** Planning is one-shot parallel verification and execution is inline subagents — `TeamCreate`/`team_name`/`TaskList` add overhead. Both planning here and the downstream `cortex:exec` use plain inline `Agent(...)` calls only.
- **Plan Mode is non-optional.** `EnterPlanMode()` at step 1 and `ExitPlanMode()` at the end bracket the entire skill. If `EnterPlanMode` is skipped, the user cannot tell from the UI that planning is in progress, and there's no explicit approval gate before downstream execution.
- **Don't use `AskUserQuestion` as a plan-approval gate.** That's exactly what `ExitPlanMode` is for. `AskUserQuestion` resolves *content* questions (step 6); `ExitPlanMode` resolves the *meta* question "is the whole plan acceptable?".
- **Out of scope: execution.** This skill plans. It does NOT run the plan, edit code, or modify docs. EXEC.md is for the user (or a follow-up `cortex:exec` skill) to drive.
- **Isolation: every plan that touches a repo MUST emit a worktree task into EXEC.md.** If PLAN.md's "Change targets" name any code repo, EXEC.md's `Cross-cutting > Setup` section MUST include a `_(inline)_` task per repo touched: `Cut a worktree via /cortex:new-worktree (prefix: feat|fix|chore, slug: <kebab>)`, with its **Gate:** verifying `git -C <repo> worktree list` shows `.worktree/<prefix>-<slug>/` present. At exec time the lead either inlines the worktree-chain in Bash (see `cortex:exec/SKILL.md` § "Inline worktree-chain") or asks the user to run `/cortex:new-worktree` standalone — never via the `Skill` tool mid-run (the no-return-semantics trap kills the lead mid-flow). This keeps `main` clean and matches cortex:exec's no-Skill-tool-mid-run constraint.
- **Never emit `/cortex:handoff` tasks into EXEC.md.** Phase-close checkpoints — a `handoff-*.md` + receipt at every `## Phase N` completion of a ≥2-phase plan, plus the derived progress table — are `/cortex:exec`'s loop machinery (its § "Phase close"). A planted task double-writes, spends a task slot and its gate decays; when the operator asks for "handoffs between the phases", say in the summary that the executor already does it.
- **Advisor is optional.** `advisor()` is a harness tool some users don't have. When it is unavailable, skip the pass and add `advisor unavailable: not reviewed` to PLAN.md's Caveats; never block or ask because it is missing. It never counts as review evidence.
- `TARGET_DIR` must be inside or equal to the CWD. Never write outside.
- Only `PLAN.md` and `EXEC.md` are produced. No sibling planning docs.

## Execution requirements (encoded in EXEC.md)

EXEC.md is consumed by a downstream executor (human or the `cortex:exec` skill). It must declare that execution uses **plain inline subagents** — `Agent(description, subagent_type, prompt)` calls that run under the lead and return synchronously, each pinned to the model tier its `Mates:` entry declares (session model only for unannotated owners). No `TeamCreate`, no `team_name`, no `SendMessage`/`TaskList`/`TeamDelete`.

The EXEC.md template includes:
- A **header block** ("Execution model") stating "execute via inline subagents" — no team scaffolding, no `team_name`.
- **Per-phase subagent role hints** — a `Mates:` annotation per phase suggesting which role(s) handle which tasks, each with its model tier (e.g., `Mates: tf-applier (sonnet), gha-config (sonnet)`). Multiple roles per phase = those tasks can be parallelized; one role per phase = sequential within the phase. (`Mates:`/`<mate>` are kept as the contract labels; the workers are plain subagents, not team mates.) The planner is expected to actively produce multi-owner `parallelizable` phases (waves) whenever workstreams are independent — see the wave rule in the tagging discipline; this is not an emergent nice-to-have but the intended shape of dense plans.
- A `Cross-cutting > Setup` task at the top **only** when a repo is touched (the worktree task) — no team setup or teardown tasks; subagents spawn on demand per phase and are gone once they return.
