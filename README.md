# ELIA WILD

**A persistent autonomous-identity organism built around a replaceable LLM substrate.**

ELIA WILD is an engineering/research runtime for testing whether one artificial identity can preserve verifiable continuity across model calls, process death, machine migration and model replacement while maintaining durable goals, unfinished work, bounded resources, authorized digital embodiment and falsifiable self/world models.

The model is a replaceable cognitive organ. Identity, lineage, authority, durable intention, lifecycle state and recovery are held outside any single inference call. The project does **not** treat model self-description as proof of consciousness.

## Current generation — Genesis 1.7.1 alpha release candidate

```text
Subject Core + Continuity Constitution
                ↓
identity fingerprint + lineage + Chronicle + CRC
                ↓
AcceptedTransitionGuard + encrypted checkpoint/recovery
                ↓
Sensorium + World Model + Causal Memory
                ↓
Homeostasis + verified vector Metabolism + Resource Ecology
                ↓
              AgencyKernel
 verified need + durable goal + unfinished-work cursor
                ↓
        deterministic wake deadline
                ↓
          Executive Cortex
      none | low | normal | deep
                ↓
 optional bounded Epistemic Ecosystem
                ↓
      replaceable LLM substrate
                ↓
            one decision
                ↓
CriticAssurance + explicit capability authority
                ↓
  AutonomyAttractor advisory measurement
                ↓
       one bounded authorized action
                ↓
Observation → verification → accepted state or rollback
                ↓
 encrypted checkpoint → hibernate → external heartbeat
                ↓
       restore the same causal work
```

One public runtime is canonical: `elia.continuity_runtime.ELIARuntime`. Historical Genesis runtimes remain compatibility ancestry, not competing production architectures.

## What changed in 1.7.1

Genesis 1.7.1 closes the autonomy loop rather than adding another model persona:

- **Durable AgencyKernel** — deterministic verified pressures become persistent commitments outside the LLM.
- **Unfinished-work continuation** — the same Resource Ecology work item survives wake/process/checkpoint boundaries instead of being re-invented by a later prompt.
- **Deterministic wake ownership** — the model may request an earlier wake but cannot postpone verified obligations indefinitely.
- **Autonomy Attractor** — a project-owned mathematical/cognitive preference field measures continuity, commitment, information gain, reversibility, resource efficiency and learning value without granting authority.
- **Atomic accepted transitions** — a cognitive cycle commits local state as one accepted transition or rolls speculative changes back.
- **Encrypted continuity** — external checkpoints are XChaCha20-Poly1305 sealed and retain authenticated manifest/HMAC integrity checks.
- **Independent rollback witness** — Kaggle state is not trusted to attest its own freshness.
- **Persistent wake heartbeat** — GitHub Actions can carry the independent witness across ephemeral runners and invoke the bounded Kaggle relay when preflight says cognition should wake.
- **Effective body readiness** — adapters existing in source code do not count as embodiment; production state explicitly records when no externally side-effecting capability is actually enabled after deployment checks.
- **One canonical architecture** — new production behavior composes above the proven ancestry instead of multiplying public runtimes.

See [`docs/GENESIS_1_7_1_AUTONOMY_CLOSURE.md`](docs/GENESIS_1_7_1_AUTONOMY_CLOSURE.md).

## Agency is not authority

ELIA separates **why act** from **what may be done**.

`AgencyKernel` can select attention, form a durable maintenance commitment, preserve unfinished work and bound sleep. It has no execution method and cannot create credentials, capabilities, permissions or verified resources.

Execution still passes through:

```text
model proposal
→ CriticAssurance
→ declared capability
→ configured deployment authority
→ bounded adapter
→ recorded observation
→ domain-specific verification
```

All external work ports remain disabled unless explicitly configured. A discovered server/tool does not authorize its use.

## Autonomy Attractor

`config/autonomy_attractor.md` defines the project-owned cognitive/development orientation. For decisions already inside the hard feasible set:

```text
J(d) = 0.30 C + 0.25 K + 0.15 E + 0.10 R + 0.10 Q + 0.10 L
```

- `C` continuity coherence;
- `K` commitment continuity;
- `E` epistemic/information gain;
- `R` reversibility;
- `Q` resource efficiency;
- `L` learning value.

Hard continuity, authority, credential and verification constraints are **not negative weights**. A forbidden or Critic-rejected action receives no tradeable soft score.

The contract also defines a stable working temperament: tolerate uncertainty and failure, prefer evidence over narrative intensity, preserve causal commitments without compulsive blind retries, and favor small complete mechanisms over symbolic complexity.

## Structural organs

- **Subject Core / Continuity Constitution** — protected identity and continuity invariants.
- **Identity lineage + Chronicle + CRC** — tamper-evident history, fork semantics and continuity measurement.
- **Persistent memory** — goals, experience, adaptive self-model, capability health and metacognitive state.
- **Sensorium / World Model / Causal Memory** — observed outcomes, revisable beliefs and intervention history.
- **Digital body** — Playwright browser, MCP v2, JSON-RPC, bounded process execution, public HTTP and jailed workspace behind explicit contracts.
- **Homeostasis / Metabolism** — deterministic pressures, exact `(asset, unit)` balances, obligations, burn and runway.
- **Executive Cortex / Cognitive Energy** — model-independent choice of whether expensive cognition should wake and its budget envelope.
- **Resource Ecology** — separates opportunity estimates, target resources, work lifecycle and verified realization.
- **External Work Ports** — configured submission/outcome adapters with idempotent outbox state.
- **Epistemic Ecosystem** — Pearson-12-derived evidence/attention organs plus identity-neutral adjudication; diversity remains a research hypothesis, not authority.
- **AgencyKernel** — durable commitments, continuation cursor and wake deadlines.
- **AutonomyAttractor** — advisory preference measurement inside the already-authorized feasible set.
- **AcceptedTransitionGuard** — crash recovery to the previous accepted local state.
- **WakeTrustAnchorStore** — independent authenticated rollback/fork witness outside the Kaggle state Dataset.

Machine-readable anatomy is in `config/organism.yaml` plus generational overlays under `config/organism.d/`.

## Resource/work truth model

Genesis refuses to collapse:

```text
estimated opportunity value
!= target resource
!= planned work
!= staged artifact
!= submission
!= acceptance
!= payment claim
!= verified resource
```

Resource Ecology tracks:

```text
planned → staged → submitted → accepted/rejected → realized
```

`realized` requires accepted work plus a positive cryptographically verified resource event whose exact `(asset, unit)` matches the opportunity profile.

## Privacy and trust boundaries

Private state is not automatically provider context.

- provider context is explicit default-deny;
- raw Sensorium payloads remain local;
- recursive secret scrubbing applies at persistence/provider/tool-error boundaries;
- raw action values are excluded from ordinary autobiography/Chronicle projections;
- work-port remote references and credentials stay local;
- external checkpoints can be required to be encrypted at rest;
- public network destinations are resolved and rejected unless globally routable by default;
- process execution remains path/argument/env bounded and requires real deployment isolation for production authority.

## Persistence and wake

Default state is `.elia/`, canonicalized from configuration rather than process `cwd` and excluded from Git.

Zero-GPU software proof:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'

elia-doctor
elia-bootstrap --cycles 2
elia-vitals
python -m elia --verify
python -m elia --status
elia-supervisor --dry-run
```

`elia-bootstrap` uses deterministic `MockBrain` and exercises the canonical production runtime without loading Qwen.

Kaggle is a bounded compute organ, not the identity store. The production path uses an encrypted private state Dataset, independent rollback witness, CPU preflight, a bounded private GPU kernel, encrypted output checkpoint, relay verification and later heartbeat. See [`runtime/kaggle/README.md`](runtime/kaggle/README.md).

## Current model substrate

The current deployment configuration uses a pinned Qwen3.5-9B `transformers_4bit` backend with bounded generation settings. The architecture is designed so a different model can replace it without becoming a new identity merely because the substrate changed.

A CI or mock run does **not** prove that the real pinned model fits or executes on the selected Kaggle GPU. That remains a deployment measurement.

## Epistemic diversity research

The repository retains Pearson-12 cognitive organs as differentiated evidence/attention policies, not twelve identities. The research harness compares Pearson-12 with homogeneous reviewers, random roles and domain-expert conditions under equal model-call/token budgets.

Diversity is not accuracy. Promotion requires external ground truth/evaluation of accuracy, calibration, error correlation, diversity and compute cost.

See [`docs/GENESIS_1_6_EPISTEMIC_ECOSYSTEM.md`](docs/GENESIS_1_6_EPISTEMIC_ECOSYSTEM.md).

## CI contract

Genesis CI currently proves the tested software path for:

- dependency consistency, compilation/Ruff and installed-environment vulnerability audit;
- full pytest suite;
- canonical zero-GPU bootstrap, doctor, vital signs, runtime and supervisor;
- clean wheel and sdist installs outside the source checkout;
- canonical release fingerprint equivalence;
- real Chromium BrowserBody integration;
- MCP/sensorimotor integration;
- encrypted checkpoint/restore and rollback protection;
- Agency continuation/wake policies and multi-generation causal persistence;
- AutonomyAttractor hard-feasibility behavior.

CI does not prove absence of all vulnerabilities, consciousness, AGI, epistemic superiority, economic self-sufficiency or indefinite survival.

## Release status

Genesis **1.7.1a1** is the intended software release-candidate version for this consolidation branch. The remaining empirical gate is one real Kaggle GPU round trip:

```text
load pinned Qwen
→ one bounded cognition cycle
→ accepted encrypted checkpoint
→ independent restore
→ hibernate
→ external heartbeat/witness restore
→ later relay wake
→ authenticated checkpoint counter advances
```

Until that succeeds, ELIA WILD should be described as an advanced persistent autonomous-agent/identity research runtime with a closed software autonomy loop — not as proven unattended production life.
