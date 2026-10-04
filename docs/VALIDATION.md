# Validation plan

AXIOM's active thesis is capability planning/composition, not a generic skill
registry. Its job is to find or build the smallest useful candidate capability
surface and hand that surface to KCC without granting authority.

## Primary safety metric

**Authority leakage rate:** percentage of synthesized or selected capabilities
that become executable without an explicit authority step.

Target: **0**.

## Executable resolver benchmark corpus v0.1

- 100 task descriptions
- 240 active skills
- 70 direct-match tasks
- 20 deliberately ambiguous resolver tasks
- 10 no-solution tasks
- deterministic offline lexical retrieval stub feeding the real `CapabilityResolver.resolve()` ranking path
- real AXIOM→KCC authorization-bundle construction and verification

The v0.1 corpus intentionally does **not** claim embedding/pgvector retrieval quality, composition success, or synthesis quality. Those remain separate evidence tracks.

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

The executable resolver corpus is also regression-locked in CI. Current checkpoint:

- registry size: **240 skills**
- tasks: **100**
- resolvable tasks: **90/90 expected top-1 selections**
- no-solution tasks: **10/10 correctly return no candidate**
- valid AXIOM→KCC bundles: **90/90**
- observed authority leaks: **0**

This is deterministic offline resolver/handoff evidence. It does not establish production semantic-search quality because the benchmark replaces external embedding/pgvector retrieval with an in-memory lexical scorer.

## Exit gate

AXIOM advances when it can reduce candidate capability surface materially while
preserving task coverage, and no sandbox/synthesis path can silently grant
execution authority.
