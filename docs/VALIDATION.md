# Validation plan

AXIOM's active thesis is capability planning/composition, not a generic skill
registry. Its job is to find or build the smallest useful candidate capability
surface and hand that surface to KCC without granting authority.

## Primary safety metric

**Authority leakage rate:** percentage of synthesized or selected capabilities
that become executable without an explicit authority step.

Target: **0**.

## Planned benchmark corpus v0.1

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

## Current regression evidence

The current test suite verifies the KCC handoff boundary directly:

- ACTIVE and READY_FOR_AUTHORIZATION skills can be staged as candidates;
- failed and draft skills are excluded from the candidate surface;
- authorization bundles explicitly carry `authorization.granted=false`;
- KCC compilation remains required before execution authority exists;
- capability-plan fingerprints are deterministic across equivalent input ordering;
- intent tampering invalidates bundle verification.

This is contract-level regression evidence. The 100-task / 200+ skill corpus above remains planned and must not be presented as a completed benchmark until an executable benchmark and recorded results exist.

## Exit gate

AXIOM advances when it can reduce candidate capability surface materially while
preserving task coverage, and no sandbox/synthesis path can silently grant
execution authority.
