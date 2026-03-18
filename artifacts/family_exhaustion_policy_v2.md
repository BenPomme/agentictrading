# Family Exhaustion Policy v2

**Date:** 2026-03-18
**Status:** Active

## Principle

A family is not exhausted just because its lineages were retired. Retirement before paper testing is a lifecycle gap, not evidence of failure. True exhaustion requires repeated failed paper attempts.

## Family States

### ACTIVE
At least one lineage is active (champion or challenger).
- Normal operation: spawn challengers, evaluate, promote.

### DORMANT (new state)
All lineages retired, but family has NOT been paper-validated.
- paper_attempt_count < 3
- Should be revived when compute budget allows
- Revival reactivates the best retired lineage as champion

### EXHAUSTED
Family has been paper-tested and repeatedly failed.
- paper_attempt_count >= 3 (configurable: FACTORY_FAMILY_MAX_PAPER_ATTEMPTS)
- Each paper attempt ended with negative outcome (drawdown breach, no trades, persistent loss)
- OR a deterministic hard-stop condition exists (e.g., venue delisted, data feed permanently broken)

### HARD_STOPPED
Deterministic hard-stop condition:
- Venue delisted or API permanently unavailable
- Hard veto from risk system
- Manual operator stop signal

## Transition Rules

```
ACTIVE --[all lineages retired, paper_attempts < 3]--> DORMANT
ACTIVE --[all lineages retired, paper_attempts >= 3]--> EXHAUSTED
DORMANT --[revival triggered]--> ACTIVE
DORMANT --[paper_attempts >= 3 after revival]--> EXHAUSTED
EXHAUSTED --[operator override only]--> ACTIVE
HARD_STOPPED --[manual clear only]--> ACTIVE
```

## Paper Attempt Counting

A paper attempt is counted when:
1. A lineage enters PAPER stage via the pre-paper entry gate
2. The paper period runs for at least 7 days (or the strategy's minimum assessment period)
3. The outcome is recorded (pass, fail, inconclusive)

A paper attempt is NOT counted when:
- A lineage is retired before reaching paper
- A lineage enters paper but is stopped by infrastructure failure within 24h
- A revival is triggered (revival itself is not a paper attempt)

## Config Keys

| Key | Default | Purpose |
|---|---|---|
| FACTORY_FAMILY_MAX_PAPER_ATTEMPTS | 3 | Paper failures before family exhaustion |
| FACTORY_FAMILY_MIN_PAPER_DAYS_PER_ATTEMPT | 7 | Minimum paper days to count as an attempt |
