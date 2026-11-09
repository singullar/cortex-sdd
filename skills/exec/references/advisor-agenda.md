# Advisor agenda — what every `advisor()` call asks, and the guardrails that bind it

`advisor()` takes no parameters: it reads the whole transcript. So the lead writes the call's **agenda** as plain text in the turn, right before the call. That keeps the agenda auditable: anyone reading the transcript sees what the advisor was asked. Every call site in the skills cites this file instead of writing its own prompt.

**`advisor()` is optional.** It is a harness tool some users do not have. When it is unavailable, skip the call and add a one-line note to the artifact's Caveats (`advisor unavailable: not reviewed`). Never block or ask because it is missing. It never counts as review evidence.

**Role.** The advisor guards consistency *together with* performance. It keeps the work proportional: no over-engineering, and nothing the acceptance criteria need is cut. It proposes and never decides, and it never trades a real risk for speed. The one bounded exception is the grant call (table below): it may add one fix round, and it never accepts, defers or stops anything. The common rule for every call: ask "should we?" before "how?".

## Over-engineering — the definition every lens and brief cites

1. **An elaborate solution** where a simpler one meets the acceptance criteria: a layer, abstraction, wrapper or configuration that no criterion asks for.
2. **Unnecessary code:** code that serves no criterion and no confirmed finding. This includes defensive branches for states that cannot occur, and narrative docblocks.
3. **Redundant tests:** tests that check the same behavior through the same path. Distinct cases (an edge, a failure, a boundary) are not redundant.
4. **Problems outside the scope:** findings about code the change neither touches nor makes worse. They are not fixed in the cycle and cost no round; at most they are noted in the delivery comment.

**What keeps this from becoming a dangerous simplification:**
- A problem the change introduces or makes worse is in scope, even when it sits in another file.
- The severity floor (guardrail 1) applies to everything the change touches.
- An out-of-scope finding in a floor area is still listed in the delivery comment. It is never left silent.

## Guardrails — they bind every call

1. **The severity floor.** The advisor may question a fix's *shape* or *cost*. It never questions whether a finding *exists*. It never downgrades a finding in a **floor area**:
   - tenant isolation;
   - authn/authz;
   - data integrity;
   - a financial or limit rule;
   - security.

   A `high` from any lens, suite-seeded failures included, becomes `deferred:risk` only by the operator's decision: the advisor may size it to choose a fix or a check, never to defer it.
2. **Unknown is not unlikely.** Every sizing cites a fact from the code or the data, as `file:line`, a query result or a documented invariant. When the precondition depends on a fact nobody has verified, the answer is a question or a check, never "accept".
3. **A symmetric agenda.** Every agenda asks both sides: "what is excess for the problem?" and "what is missing for the acceptance criteria?". An agenda with only one side is malformed, and the lead rewrites it before calling.

## The agendas, per call site

| Call site | When | The agenda asks | Blocking means |
|---|---|---|---|
| **Explore** (`skills/explore/SKILL.md`) | Only when a decisive fact may be missing. No such fact means no call. | "Which decisive fact is missing, and who answers it now?" · "Is this the minimum scope that meets the problem?" · "What does the problem need that the scope leaves out?" | Nothing blocks. Each decisive fact becomes a front-loaded open question. |
| **Plan** (`skills/plan/SKILL.md`) | Always, before the plan is shown. | The smallest plan that meets the criteria, and what to cut · where the cycle can stall (a decision without an owner, a missing fact, an access the exec needs) · each risk sized as likelihood × impact · which acceptance criterion the plan does not reach | **Only** an unmet acceptance criterion, an untreated high risk, or a contradiction with a recorded decision. "Could be more robust" is a note. |
| **Review row** (`review-protocol.md`, the `advisor` matrix row, the proportionality lens) | Once per review, never in fix rounds. | What in the diff is over-engineering by the definition above · what the acceptance criteria still miss · which findings from the other lenses cost more to fix than the risk they carry (within the floor) | Its findings enter dedup like any reviewer's. |
| **Sizing** (`review-protocol.md` fix loop, at most one call per round) | A round holds a concurrency, ordering, retry, edge or partial-fix finding, or the cap left a blocking finding the grant call did not size (the `Review round` question). An item recorded `unsized: advisor unreachable` counts as sized for this trigger: the row never fires for it. | One call covers all of them, and each finding is sized on its own: is the precondition realistic (cite the fact)? what is the impact? is the fix partial? does it add a new failure mode? and, the other side, what does an acceptance criterion need from this fix that it would miss? | Per finding: fix, or `deferred:risk` with its sizing (never for a floor-area or `high` finding: guardrails 1–2), or a question or check when the fact is unknown. |
| **Grant** (`review-protocol.md` § "Advisor round", at most one call per review; a resume calls again only while no `advisor_grant` is recorded) | The cap classification left blocking items, before the `Review round` question (criterion (a), the lead's check). | Each blocking item sized as in the Sizing row, then each criterion with its cited fact: (b) is every item a concrete fix inside the plan — not partial, no new failure mode, no new scope? (c) is the suite green at the head? (d) does any fix have an irreversible step? (e) would the round be the 3rd causal oscillation in one `fix_areas` entry? and, the other side, what does an acceptance criterion need that the round would still miss? | Grant only when (b)–(e) all hold; an unknown is not met. Its sizing only informs the round and the `Review round` question: it never produces `deferred:risk`, and the call never accepts, defers or stops. Not granted, the `Review round` question decides. |

The agenda is written as a short block that opens with `Advisor agenda —`, followed by the call site and its questions. Both sides of guardrail 3 must be present.
