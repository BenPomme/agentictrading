# Cost Control and Multi-Agent Policy
## Production governance for an autonomous trading-research factory

## Purpose

This document defines the mandatory policy for controlling spend, constraining multi-agent execution, and forcing learning efficiency in AgenticTrading after migration to:

- mobkit runtime orchestration
- Meerkat agent harnessing
- Goldfish provenance storage

This is a policy document, not a suggestion list. The implementation agent must treat these controls as enforceable runtime rules.

---

## Design objective

The goal is **compound learning per unit cost**, not “maximum agent activity.”

A cheaper system that learns from failures, avoids repeated dead ends, and promotes only high-signal candidates is preferable to a richer system that spends aggressively without improving selection quality.

---

## Budget hierarchy

The system must implement **five active budget layers**.

## 1. Global budget
Applies to the whole factory process per day.

Recommended controls:
- total token ceiling
- total USD-equivalent ceiling
- max concurrent mob workflows
- max autonomous creation cycles per day

Global hard-stop conditions:
- budget exceeded
- repeated orchestration instability
- repeated invalid schema output after retries
- operator emergency stop

## 2. Family budget
Applies per strategy family.

Recommended controls:
- family daily token ceiling
- family daily USD-equivalent ceiling
- max new lineages/day
- max critique depth/day
- max expensive runs/day

Family hard responses:
- disable new-family ideation
- allow only mutation on active candidates
- force cheap reviewer tiers
- pause deep-review workflows

## 3. Lineage budget
Applies to one lineage or candidate.

Recommended controls:
- total spend before retirement or promotion decision
- max number of mutations
- max number of failed backtests before forced retirement
- max critique rounds

Lineage hard responses:
- force terminate the lineage
- record retirement rationale
- prohibit further mutation without manual override

## 4. Task budget
Applies to one runtime task or workflow.

Required controls:
- max tokens
- max runtime seconds
- max retries
- max tool calls
- max subordinate members
- output schema retry limit

Task hard responses:
- downgrade model
- reduce member count
- collapse into single structured task
- fail fast and record reason

## 5. Mob-member budget
Applies to one member inside a mob workflow.

Required controls:
- per-member max tokens
- tool whitelist
- timeout
- allowed provider/model tier
- spawn permissions
- read/write permissions

Member hard responses:
- cancel member
- continue synthesis without that member
- fallback to cached reviewer logic
- fail workflow only if the member is marked critical

---

## Model tier policy

Define model tiers by intent, not by vendor branding.

## Tier 0: deterministic / no-LLM
Use for:
- parsing
- formatting
- static transformations
- rule-based checks
- deterministic scoring
- basic validation

## Tier 1: cheap reviewer
Use for:
- critique
- syntax sanity review
- basic feasibility checks
- alternative hypothesis enumeration
- failure categorization

These members should be the default supporting members in a mob.

## Tier 2: standard worker
Use for:
- structured proposal generation
- code mutation
- post-evaluation interpretation
- operational diagnostics

## Tier 3: expensive lead
Use only for:
- high-stakes synthesis
- borderline promotion decisions
- difficult diagnosis after repeated failures
- initial high-value family ideation when budget permits

## Tier 4: restricted / exceptional
Use only with explicit policy permit:
- deep technical reviews
- high-context audit runs
- rare rescue paths where lower tiers failed and the expected value remains positive

Tier 4 should be globally rate-limited and family-limited.

---

## Default multi-agent pattern

The default mob should be **one capable lead + several cheap narrow reviewers**.

### Approved default pattern
- 1 lead synthesizer
- 1 cheap critic
- 1 cheap feasibility reviewer
- 1 cheap policy / budget referee

### Default intent
Use cheap members to widen search and challenge assumptions.
Use the lead only to synthesize the final structured output.

### Disallowed default pattern
- multiple expensive members on the same task
- multiple members with broad tool access
- recursive member spawning
- unconstrained reviewer commentary with no schema requirement

---

## When to use a mob versus a single structured task

## Use a mob when:
- the task benefits from adversarial critique
- the task mixes creativity and constraint
- there is a meaningful separation between proposer and reviewer
- failure to challenge assumptions is expensive
- the target output has clear schema and decision semantics

Examples:
- strategy proposal
- code design / mutation review
- backtest critique
- maintenance diagnosis

## Use a single structured task when:
- the task is simple transformation
- the task is deterministic or near-deterministic
- the added coordination cost outweighs review benefit
- there is no meaningful diversity of perspective

Examples:
- formatting
- small metadata extraction
- direct summary of a local artifact
- straightforward config generation

---

## Tool access policy

## Lead member
May receive:
- read access to local artifacts
- relevant Meerkat tools
- tightly scoped Goldfish interaction via orchestrator
- no unrestricted shell unless explicitly required and tested

## Cheap critic
May receive:
- structured context
- read-only tools
- no write-capable code tools by default
- no shell by default

## Code author
May receive:
- code generation / patch tools
- local project read access
- targeted write access limited to designated files
- test invocation only if task permits

## Policy referee
May receive:
- budget ledger snapshot
- family policy
- task contract
- no code mutation tools

---

## Retry policy

Retries are expensive. They must be bounded.

### Structured output retries
- default: 1
- maximum: 2
- if schema repeatedly fails, downgrade or fallback instead of looping

### Workflow retries
- default: 0 for expensive workflows
- default: 1 for cheap workflows
- any retry must record failure reason and changed parameters

### Goldfish write retries
- allow small bounded retry count for transient write issues
- never silently drop provenance

---

## Downgrade cascade

When a workflow threatens or exceeds budget, the runtime must apply a deterministic downgrade cascade.

### Ordered downgrade path
1. lower output token limit
2. reduce reviewer count
3. replace expensive reviewers with cheap reviewers
4. disable non-essential tools
5. collapse to single structured task
6. switch to legacy or deterministic fallback if permitted
7. stop the task and log a policy stop

### Required metadata
Every downgrade event must record:
- prior tier
- new tier
- reason
- budget scope affected
- operator severity
- whether output quality is now degraded

---

## Family-level throttling policy

A family enters **throttled mode** when it exceeds a soft threshold and **paused mode** when it exceeds a hard threshold.

## Soft threshold actions
- no new family ideation
- cheap reviewer-only support members
- lower max tokens
- no deep-review path

## Hard threshold actions
- no autonomous creation
- only complete already-running evaluations
- force retirement of clearly dominated lineages
- require next UTC/day reset or manual release

---

## Lineage retirement policy

A lineage must be retired when:

- cumulative spend exceeds its allowed budget and no promotion case exists
- repeated mutations fail without meaningful improvement
- failure modes repeat and Goldfish memory indicates low expected value
- the lineage consumes critique cycles disproportionate to evidence

Every retirement must write:
- retirement reason
- cost summary
- best achieved metrics
- what was learned
- what future lineages should avoid

This must be written both to:
- Goldfish durable memory / record tags
- local projection cache during migration

---

## Promotion policy

Promotion must require:
- validated performance evidence
- critique workflow completion
- cost summary
- robustness flags
- lineage spend summary

A candidate must not be promoted if:
- the promotion decision itself exceeded allowed exceptional budget without approval
- provenance is incomplete
- dataset identity is missing
- code hash is missing
- the post-evaluation critique remains inconclusive with unresolved critical flags

---

## Learning-efficiency policy

Autonomy is only valuable if the system avoids repeating failure classes.

The system must therefore do the following after each retired lineage:

1. summarize failure pattern
2. attach the pattern to Goldfish
3. update local projection cache
4. make that failure pattern available to future proposal and mutation workflows
5. reduce probability of re-exploring the same dead zone

### Required memory categories
- feature construction failure
- overfit regime
- unrealistic turnover or cost behavior
- data leakage suspicion
- invalid execution assumptions
- code instability
- evaluation mismatch
- family-level structural weakness

---

## Circuit breakers

## Global circuit breaker
Trips when:
- daily budget breached
- systemic schema failure
- runtime orchestrator instability
- provenance write failures exceed threshold

Response:
- halt all autonomous creation
- allow only safe shutdown / state finalization

## Family circuit breaker
Trips when:
- family overspend
- repeated null/invalid outputs
- repeated lineage failures of same class

Response:
- disable family creation and mutation
- preserve existing records
- surface operator alert

## Runtime circuit breaker
Trips when:
- mobkit backend unhealthy
- Meerkat runtime unavailable
- fallback rate above threshold

Response:
- revert to configured fallback backend or pause LLM work

---

## Observability requirements for cost governance

Every workflow must emit:
- planned budget
- actual usage
- downgrade actions
- member-level usage
- fallback use
- final policy state

Every dashboard or operator summary must expose:
- remaining global budget
- top-spending families
- most expensive workflows
- fallback frequency
- Goldfish write health
- current backend

---

## Default policy recommendations

These defaults are intentionally conservative.

- global daily autonomous creation budget: low to moderate
- family daily cap: tight
- lineage cap: very tight for early-stage candidates
- lead member: one only
- reviewer members: cheap only
- deep review: rare and policy-gated
- schema retries: minimal
- workflow retries: minimal
- recursive mobs: disabled
- broad shell access: disabled by default

---

## Definition of policy success

The cost policy is working when:

- spend is predictable
- fallback behavior is deterministic
- failed lineages terminate earlier
- repeated failure classes drop over time
- promotion decisions carry complete provenance
- operator dashboards can explain where cost went and why
