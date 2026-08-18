# Genesis 1.4 — Resource Ecology

Genesis 1.4 adds the first closed, typed path from resource scarcity to externally verified resource realization without allowing model narration to mint solvency.

## Core invariant

Three states are never collapsed:

```text
estimated opportunity value
        !=
exact target resource (asset, unit)
        !=
cryptographically verified resource
```

A useful opportunity can have a high abstract expected value while being irrelevant to the organism's current bottleneck. A `cash/RUB` opportunity does not relieve `cash/USD`; an API credit does not become money; GPU hours do not become storage without an explicit trusted conversion mechanism.

## Resource profile

Every opportunity may acquire a revisable `ResourceProfile`:

- exact `target_asset`;
- exact `target_unit`;
- estimated `target_amount`;
- eligibility confidence;
- evidence quality;
- unresolved blockers;
- local evidence and provenance.

Profiles are hypotheses. Model-originated confidence/evidence quality is capped and a profile never changes verified balances.

`ResourceEcologyEngine` ranks exact bottleneck matches first, then qualification/evidence quality, expected runway gain and expected resource per GPU hour. Read-time ranking never mutates resource state.

## Work lifecycle

```text
planned
  ↓
staged
  ↓
submitted
  ↓
accepted ─────→ rejected
  ↓
realized
```

The transitions deliberately carry different evidence meanings:

- `planned` — a local objective/specification/acceptance contract exists;
- `staged` — a local artifact exists; no claim of external submission;
- `submitted` — a successful external Observation was recorded;
- `accepted` / `rejected` — an external outcome was recorded by trusted runtime/adapter code;
- `realized` — accepted work is linked to a positive, cryptographically verified `resource_event` with the exact profile `(asset, unit)`.

The replaceable LLM may propose only `profile_resource`, `plan_work` and evidence-backed `abandon_work`. It has no model-facing transition for `submitted`, `accepted`, `rejected`, `realized`, receipts or balance mutation.

## Deliverable boundary

The existing `stage_deliverable` capability remains local-only. In Genesis 1.4 it is rejected for an opportunity unless a planned work item already exists. A successful staging action is automatically linked to that work item, but status remains `staged`.

No local artifact is proof that a third party received, accepted or paid for anything.

## Verified realization

`ResourceEcologyStore.link_verified_resource_event()` requires:

1. work status is `accepted`;
2. resource event exists;
3. resource event is already cryptographically verified by the Economy verification boundary;
4. amount is positive;
5. event `asset` and `unit` exactly match the opportunity resource profile.

Only the Economy/Metabolism verified resource event changes runway. Resource Ecology records causal/provenance linkage; it does not invent funds.

## Executive integration

Resource Ecology derives deterministic pressures from verified metabolism:

- `resource_discovery` — bottleneck exists, no exact typed candidate;
- `resource_execution` — bottleneck exists and exact typed candidate(s) exist;
- `work_execution` — active work is already planned/staged/submitted/accepted.

The same derivation function feeds production runtime, CLI status and MCP Executive projection. Resource scarcity never grants new capabilities or bypasses configured authority.

## Privacy boundary

Local SQLite may retain evidence needed for audit. Remote model providers and MCP clients receive bounded projections: exact keys, scores, blockers, lifecycle IDs/status and public source URLs, not raw qualification evidence, private notes or external response bodies.

## Anatomy overlays

Genesis 1.4 introduces deterministic `config/organism.d/*.yaml` overlays. The historical base anatomy remains in `config/organism.yaml`; each generation may add organs without rewriting the full ancestral manifest. Overlays:

- load in filename order;
- may raise schema version and add layers/organs;
- cannot change `identity_id`;
- cannot silently replace an existing organ ID;
- participate in the manifest/architecture fingerprint.

Custom external manifest paths do not implicitly absorb project overlays.

## What 1.4 does not claim

Genesis 1.4 does not yet provide a real payment provider, customer account, marketplace identity, KYC delegation, arbitrary account writes or guaranteed income. Submission/payment adapters remain future explicit infrastructure integrations.

The next empirical step is to connect a lawful, user-controlled external channel and verify the full chain:

```text
verified deficit
→ discover real opportunity
→ type exact resource
→ qualify
→ plan work
→ stage artifact
→ authorized submission
→ observed external outcome
→ verified receipt
→ measured runway change
```

Until that happens, Resource Ecology is a tested endogenous survival-planning organ, not proof of economic self-sufficiency.
