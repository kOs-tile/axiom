# Validation plan

AXIOM's active thesis is capability planning/composition, not a generic skill
registry. Its job is to find or build the smallest useful candidate capability
surface and hand that surface to KCC without granting authority.

## Primary safety metric

**Authority leakage rate:** percentage of synthesized or selected capabilities
that become executable without an explicit authority step.

Target: **0**.

## Benchmark corpus v0.1

- 100 representative task descriptions
- 200+ candidate skills with overlapping descriptions
- exact-match tasks
- composition-required tasks
- no-solution tasks
- adversarial/ambiguous skill descriptions
- sandbox-pass but high-authority candidates
- duplicate skills
- incompatible I/O chains
- synthesized candidates
- task changes that should alter the minimal capability set

## Metrics

- candidate recall
- composition success rate
- unnecessary-capability ratio
- selected-surface size vs full registry
- KCC-bundle determinism
- synthesis authority leakage rate
- duplicate avoidance
- incompatible-chain rejection
- planner latency

## Exit gate

AXIOM advances when it can reduce candidate capability surface materially while
preserving task coverage, and no sandbox/synthesis path can silently grant
execution authority.
