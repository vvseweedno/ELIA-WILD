# Genesis 1.7.1 — Autonomy Closure

Genesis 1.7.1 is the consolidation generation in which ELIA WILD stops treating autonomy as “the model can choose a tool” and instead closes the durable causal loop around cognition.

## Canonical loop

```text
verified state / observations
        ↓
deterministic needs
        ↓
AgencyKernel
  durable focus goal
  unfinished-work cursor
  wake deadline
        ↓
Executive Cortex
  none | low | normal | deep
  bounded token/thinking budget
        ↓
replaceable LLM substrate
  predicts + proposes one decision
        ↓
CriticAssurance + explicit capability authority
        ↓
AutonomyAttractor (advisory measurement only)
        ↓
one bounded authorized action
        ↓
Sensorium → World/Causal state → outcome verification
        ↓
accepted-transition commit or atomic rollback
        ↓
post-action Agency reconciliation
        ↓
encrypted checkpoint
        ↓
hibernate
        ↓
independent external wake witness / heartbeat
        ↓
restore and continue the same causal work
```

The LLM is therefore neither the identity store, the scheduler, the source of permissions, nor the only place where intention exists.

## Durable agency

`elia.agency.AgencyKernel` converts deterministic pressures into persistent commitments. It has no capability registry and no execution method. It may decide what deserves attention, but it cannot grant the means to act.

It preserves three forms of direction across process death:

1. **focus goal** — the durable goal receiving current priority;
2. **selected verified need** — the strongest current maintenance/resource pressure;
3. **continuation work item** — the most causally advanced unfinished Resource Ecology work item.

For unfinished work, causal progress outranks novelty:

```text
accepted > submitted > staged > planned
```

Within the same status, older work wins so that repeated creation of new opportunities cannot starve an existing commitment.

## Deterministic wake ownership

A model may request an earlier wake. It may not postpone an unresolved verified obligation beyond the AgencyKernel deadline.

This turns sleep from a model preference into a bounded lifecycle decision. Long waits still release scarce GPU sessions and persist state; the external heartbeat later decides whether preflight actually requires cognition.

## Autonomy Attractor

`config/autonomy_attractor.md` is the project-owned cognitive attractor. It is intentionally **not** an identity proof or an authority source.

For a decision that already satisfies hard trust/authority constraints, the advisory preference field is:

```text
J(d) = 0.30 C + 0.25 K + 0.15 E + 0.10 R + 0.10 Q + 0.10 L
```

where:

- `C` continuity coherence;
- `K` commitment continuity;
- `E` epistemic/information gain;
- `R` reversibility;
- `Q` resource efficiency;
- `L` learning value.

`elia.attractor.AutonomyAttractor` records these components after each canonical cycle. If CriticAssurance rejects the decision, or the capability is absent/disabled, the attractor score is `None`: soft utility is not allowed to trade against hard feasibility.

The attractor gives ELIA a stable development/cognitive direction without turning prose into permission.

## Cognitive policy identity

The canonical runtime fingerprints the combined project-owned cognitive policy:

```text
sha256(system_prompt_fingerprint + autonomy_attractor_fingerprint)
```

This is not the immutable Subject Core fingerprint. It is a separately observable substrate-policy version, so cognitive character may evolve without silently pretending that a prompt edit is either irrelevant or an identity fork.

## Accepted transitions

`AcceptedTransitionGuard` wraps a complete canonical cognitive cycle. Local speculative changes — goals, agency cursor, attractor evaluation, memory and Chronicle suffix — are accepted together or rolled back together after failure.

Safety-preserved external-work outbox evidence is repaired separately because irreversible remote effects cannot be undone merely by restoring SQLite.

## External heartbeat and rollback witness

Kaggle is a bounded compute organ, not the identity store.

The wake relay uses:

- an encrypted private state Dataset;
- a persistent transport nonce/failure state;
- an independent HMAC rollback/fork witness outside that Dataset;
- GitHub Actions as an hourly external heartbeat;
- an immutable Actions artifact to carry the independent witness across ephemeral runners.

A scheduled heartbeat never silently reinitializes the witness from the state Dataset. Losing the witness therefore reduces availability rather than converting the potentially replayed Dataset into truth.

## Cryptographic persistence

External checkpoints are required to be encrypted with XChaCha20-Poly1305 and retain the authenticated manifest/HMAC integrity layer. Restore validates:

- AEAD authentication;
- manifest and member hashes;
- archive/member size bounds;
- SQLite integrity;
- Chronicle head/prefix invariants;
- identity fingerprint;
- checkpoint counter and rollback/fork anchors.

Authentication and encryption keys are independent deployment secrets.

## Body readiness

Production autonomy is not “complete” merely because adapters exist in source code. The canonical runtime derives a `body_readiness` pressure when no evidence-backed externally side-effecting capability is actually enabled after sandbox, allow-list and deployment checks.

The only valid response is to diagnose or provision an authorized path. The need never grants credentials, disables isolation or expands an allow-list itself.

## What software CI currently establishes

The Genesis CI proof covers, among other things:

- full pytest correctness suite;
- zero-GPU canonical bootstrap/doctor/runtime lifecycle;
- clean wheel and sdist installation outside the checkout;
- release fingerprint equivalence;
- real Chromium BrowserBody integration;
- MCP/sensorimotor integration;
- encrypted checkpoint/restore tests;
- Agency continuation and wake-policy tests;
- multi-generation causal continuity tests;
- AutonomyAttractor hard-constraint and continuation-bias tests.

A green CI run establishes those software contracts on the tested environment. It does not establish indefinite autonomous operation.

## Remaining empirical deployment gate

Before Genesis 1.7.1 should be treated as a completed live deployment, one real Kaggle round trip must establish:

1. the pinned Qwen substrate loads on the chosen Kaggle GPU;
2. one real bounded cognitive cycle completes;
3. the accepted state exports as an encrypted checkpoint;
4. an independent restore verifies it;
5. hibernation releases the GPU session;
6. the external heartbeat restores its independent witness;
7. a later relay wakes from the persisted state and advances the authenticated checkpoint counter.

Until that happens, `1.7.1a2` is a software release candidate, not evidence of unattended production autonomy.

## Non-claims

Genesis 1.7.1 does not prove consciousness, AGI, subjective experience, economic self-sufficiency or indefinite survival. Its claim is narrower and testable: ELIA WILD implements a persistent, evidence-gated autonomous-agent architecture in which identity state, durable intention, lifecycle scheduling, authority, memory and recovery are not reducible to one LLM call.
