# ELIA WILD

**Persistent autonomous-identity organism around a replaceable LLM substrate.**

ELIA WILD is an engineering/research attempt to make artificial identity continuity, autonomy, embodied digital interaction, resource physiology and self-maintenance **persistent outside a chat session and outside any one model checkpoint**.

The falsifiable question is not “can a model say that it is alive?” It is:

> Can one artificial identity preserve verified lineage and behavior-changing state across model calls, process death, machine migration and model replacement; perceive and act through a configured digital body; maintain revisable world/self models; allocate scarce cognition; learn from observed outcomes; maintain goals and resources; wake itself through an external supervisor; and evolve its software body without silently rewriting the identity contract?

## Genesis 1.3 alpha candidate

```text
Subject Core + Continuity Constitution
                 ↓
      identity fingerprint + lineage
                 ↓
 organism anatomy audit + CRC vital signs
                 ↓
 resident CPU supervisor / external wake relay
                 ↓
       deterministic lifecycle preflight
        ├── halt      → preserve evidence
        ├── hibernate → do not load model
        └── wake
                 ↓
  HOMEOSTASIS + VERIFIED METABOLISM
                 ↓
       DETERMINISTIC EXECUTIVE
        ├── halt / hibernate → no LLM
        ├── maintenance
        ├── resource
        ├── mission
        └── observe
                 ↓
 cognitive tier + token/thinking envelope
                 ↓
         lazy replaceable LLM brain
                 ↓
 Sensorium + World Model + Causal Memory
 + recall + self-model + goals + economy
 + skills + capability health + digital body
                 ↓
       pre-action prediction + critic
                 ↓
          one bounded action
                 ↓
     normalized Observation + State Bus
                 ↓
 outcome → calibration → measured brain cost
                 ↓
 Executive energy feedback + memory/world update
                 ↓
       next wake + authenticated checkpoint
```

The model is one organ, not the identity. External adapters are replaceable organs, not implicit permissions. Scarcity and cognitive cost are measured state variables, never authority-escalation mechanisms.

## What is core now

- **Subject Core** — immutable identity invariants and epistemic boundaries.
- **Continuity Constitution** — precedence, lineage, migration and revision rules.
- **Project-owned cognitive contract** — model-facing operating contract in `config/system_prompt.md`.
- **Machine-readable anatomy** — `config/organism.yaml` distinguishes required organs, replaceable body adapters and research organs.
- **Vital signs / CRC** — organism audit + Continuity Record Capsule; broken structural continuity blocks model loading.
- **Chronicle** — append-only SHA-256 hash chain with single-writer locking, durable flush and persistence-boundary action redaction.
- **Persistent SQLite state** — memories, goals, capability health, self-hypotheses, metacognitive forecasts, economy, metabolism, Executive history, lineage, sensorium, world beliefs, intervention history and state-bus events.
- **Observation Store / Sensorium** — every capability outcome is normalized below the LLM with provenance and a content digest; aged raw payloads compact while preserving evidence digests.
- **ProviderContext boundary** — remote model providers receive an explicit projection; raw Sensorium payloads and private runtime keys do not cross that boundary.
- **World Model** — revisable external beliefs with visible contradictions; model hypotheses cannot self-promote to verified facts.
- **Causal Memory** — intervention/outcome history and empirical strategy statistics explicitly separated from causal proof.
- **Organism State Bus** — hash-chained causal transition history with SQLite-transactional sequencing and interrupted-transaction recovery.
- **Verified Metabolism** — independent `(asset, unit)` balances, recurring obligations, measured GPU energy and deterministic bottleneck/runway state.
- **Homeostasis** — deterministic maintenance pressures from storage, transition integrity, sensor health, epistemic conflict, body readiness and verified resource runway.
- **Executive Cortex** — deterministic pre-LLM arbitration over continuity, verified needs and durable goals; it can suppress cognition entirely or assign a bounded cognitive tier.
- **Cognitive Energy Controller** — compares planned vs actual brain-seconds and can only constrain later inference after repeated overspend; cheap calls never auto-upgrade cognition.
- **ExecutiveStore** — commits the chosen focus/budget before inference and later resolves it against measured model cost and outcome.
- **Verification receipts** — verified resource/obligation mutations require a signed receipt over the exact normalized claim and evidence; a caller-provided authority string is not authority.
- **Semantic recall / adaptive self-model / metacognition** — memory retrieval, revisable hypotheses and pre-action calibration remain separate from immutable identity.
- **CriticAssurance / IdentityDriftMonitor** — deterministic authority/evidence/continuity checks after cognition and before bounded action.
- **Skills / Capabilities** — procedures never grant permissions; executable authority stays in explicit configured capabilities.
- **ResidentSupervisor / checkpoints / wake relay** — model-independent lifecycle, authenticated migration and guarded external wake for ephemeral compute.
- **Body revision gate** — mutation proposal, tests, signed evaluation, validation and deployment remain separate stages.

## Executive Cortex and cognitive energy

Genesis 1.3 no longer asks the LLM to decide whether the LLM should have been called.

Before inference, deterministic state is reduced to an Executive plan:

```text
continuity + resource budget + homeostatic needs + durable goals
                             ↓
                       ExecutivePlan
                             ↓
 mode: halt | hibernate | maintenance | resource | mission | observe
 cognitive tier: none | low | normal | deep
 token ceiling
 adaptive-thinking permission
 target brain-seconds
```

Default cognitive envelopes are configured in `config/genesis.yaml`:

```text
none      no model call
low       256 tokens   adaptive thinking off
normal    640 tokens   adaptive thinking off
deep      1024 tokens  adaptive thinking permitted
```

The configured model maximum remains the outer ceiling. Cycle-local token/thinking changes are restored in `finally`, including inference failures.

Actual brain-seconds are recorded after the cycle. Repeated cost overshoot may downgrade later `deep → normal → low`. The feedback controller is deliberately asymmetric: unusually cheap calls cannot automatically increase inference depth.

Set `executive.enabled: false` to exercise the retained Genesis 1.2 metabolic behavior as a feature-level rollback. This changes software policy, not identity lineage.

See `docs/GENESIS_1_3_EXECUTIVE.md`.

## Digital body

Install the full sensorimotor extras:

```bash
pip install -e '.[sensorimotor]'
python -m playwright install chromium
```

External organs are **disabled by default**. Enabling one is an infrastructure-owner action, not a model action.

Implemented adapters:

- Playwright browser contexts: navigate/snapshot/screenshot plus separately gated click/fill interaction; enabling requires a deployment network-isolation attestation and interaction requires an origin allow-list;
- MCP v2 client: configured servers, discovery, resource reads and allow-listed tool calls; automatic redirects are disabled and credentialed public transport requires an isolation boundary;
- MCP v2 server: `elia-mcp`, stdio by default and loopback-only Streamable HTTP; it exposes sanitized status plus the current read-only Executive projection;
- local processes: explicit executable aliases, `shell=False`, bounded IO/timeouts, and a required external sandbox command in production; unisolated execution exists only as an explicit unsafe development/test mode;
- JSON-RPC 2.0: configured endpoints/method allowlists over bounded DNS-pinned POST transport;
- public HTTP reads over DNS-pinned transport with validated destination IP + connected-peer recheck and no automatic redirects;
- private workspace capabilities with path-jail enforcement.

The model cannot turn a discovered server, executable, endpoint or tool into new authority by mentioning it.

See `docs/GENESIS_1_1_BODY_WORLD.md`, `docs/GENESIS_1_2_METABOLISM.md` and `docs/GENESIS_1_3_EXECUTIVE.md`.

## Experience changes later cognition

ELIA does not rely on the model to say “remember this.” Capability execution automatically produces:

```text
action
→ Observation
→ intervention record
→ State Bus transition
→ outcome
→ future world/memory state
```

Genesis 1.3 adds a second feedback loop:

```text
Executive target brain-seconds
→ actual inference cost
→ ExecutiveStore
→ CognitiveEnergyController
→ later cognitive budget
```

The organism therefore has a testable mechanism by which lived computational cost changes future thinking policy without changing Subject Core.

## Privacy and provenance

Full normalized outcomes live in private Sensorium while fresh, then age into digest-preserving compact records. Raw action arguments/tool payloads are independently redacted at **MemoryStore, Chronicle and Metacognition persistence boundaries**. Remote model backends receive `ProviderContext`, where raw Sensorium payloads and private runtime keys are removed before serialization.

Transport credentials are scoped to the smallest process/step that requires them. In the Kaggle relay, `ELIA_CHECKPOINT_KEY` is not delegated to the external Kaggle CLI child process.

## Verified metabolism

Resource pressure is deterministic, not model-authored. Each `(asset, unit)` remains independent:

```text
verified_daily_burn = Σ obligation_amount × 86400 / cadence_seconds
runway_days = max(0, verified_balance) / verified_daily_burn
```

`100 USD`, `100000 RUB`, `5 API CREDIT` and `10 GPU HOUR` are not silently collapsed into one scalar. Verified resource/obligation state requires a signed `VerificationReceipt` checked against a trusted `VerificationRegistry`. Tampering with the signed claim or evidence invalidates verification.

Verified obligation mutations such as advancement or deactivation are receipt-gated too; the organism cannot improve its runway by merely declaring a bill gone. Unverified obligations remain inspectable but do not create survival pressure.

## Research lineage preserved, not silently enabled

The repository carries maturity-labelled research from the ELIA / Seraphim / Holo / Omega line:

- Ouroboros/x0 hidden-state injection with silver/half/learned/octagonal decay;
- TopologicalLoss reference objective;
- ScrollMemory → surprisal-gated FractalMemory;
- LRU associative-scan baseline and Holo scan research backend;
- StatefulMemoryCache;
- ContextAnchor, bounded-depth FiLM, OmegaFilter and TriCore;
- PASB/CriticAssurance and structural identity drift monitoring;
- needle, associative-transitivity, generation-stability and scrambled-pattern evaluators;
- RuntimeCompatibilityChecker, DatasetCocktailRegistry and SmokeFirstRunner extracted from archived TPU/Kaggle failure lessons;
- common memory-backend ablation harness.

Full complex Holo, ComplexRMSNorm, HybridOptimizer and other hypotheses remain outside Genesis defaults until controlled evidence justifies promotion. See `docs/RESEARCH_LINEAGE.md` and `docs/EVOLUTION_PROTOCOL.md`.

## Zero-GPU proof path

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'

elia-doctor
elia-bootstrap --cycles 2
elia-vitals
python -m elia --status
elia-supervisor --dry-run
```

`elia-bootstrap` explicitly uses deterministic `MockBrain` and now exercises the Genesis 1.3 Executive runtime, including Sensorium/World/Causal/StateBus/Metabolism/Executive wiring, without spending GPU quota.

## Production cognition path

Default brain configuration is pinned to:

```text
Qwen/Qwen3.5-9B
model revision e0330a142393d4516eca6ab0145ce66ac513e842
Transformers source revision bea0343fca1fc64bb4cf91fe09143ea386e6270f
bitsandbytes NF4 4-bit
base thinking disabled
lazy load after lifecycle/vitals and Executive decision
```

For a direct one-cycle GPU smoke test:

```bash
pip install -e '.[gpu]'
python -m elia --preflight
python -m elia --cycles 1
python -m elia --vitals
python -m elia --status
```

`target_brain_seconds` is currently an audited adaptive target, **not a hard wall-clock kill switch** for local Transformers inference. The real hard envelope currently comes from token caps, provider/runtime timeouts and external Kaggle kernel timeout. This distinction is intentional and documented rather than implied away.

## ELIA MCP port

```bash
pip install -e '.[mcp]'
elia-mcp --transport stdio
elia-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Genesis 1.3 rejects non-loopback MCP HTTP binding because this server does not pretend to provide a remote authentication policy it does not implement. Use an explicit authenticated proxy/tunnel for remote exposure.

Read-oriented tools include `elia_status`, `elia_preflight`, `elia_executive`, world query, recent sanitized Sensorium, body diagnostics and homeostasis.

## Persistence and migration

Private state is canonicalized relative to project/configuration rather than process `cwd`, so launching the same configuration from another directory does not silently create a second state root. `.elia/` is excluded from Git and contains SQLite state, Chronicle, checkpoint anchor, private workspace and organism vitals.

The example systemd service uses a dedicated `elia` user, private umask, `NoNewPrivileges`, read-only system protection and additional kernel/control-group restrictions.

Authenticated export:

```bash
export ELIA_CHECKPOINT_KEY='<long-random-secret>'
python -m elia --checkpoint-export /private/elia.eliacp
```

Checkpoint export is fail-closed if Chronicle, logical SQLite contents or workspace change during capture. A fresh machine restores and verifies state **before** cognition.

A body/prompt/model upgrade is observable mutation, not automatic identity death. Immutable identity/core/constitution/branch and Chronicle continuity remain the critical CRC boundary.

## Continuous existence

Two model-independent wake mechanisms exist:

1. `elia-supervisor` — resident workstation/VPS/systemd pulse that checks vital signs/lifecycle and launches a fixed cognitive child only when due.
2. `.github/workflows/wake.yml` — guarded hourly heartbeat for the private Kaggle relay, disabled until user-controlled secrets/resources are configured.

Long sleeps become `HIBERNATE` and release scarce GPU allocation instead of idling inside a notebook session.

## Self-evolution without self-certification

`BodyRevisionStore` persists mutation hypotheses, target organs, expected metrics, regression and rollback plans. `RevisionGate` computes deterministic test/vitals/CRC/metric predicates but does not deploy code.

A revision can be recorded as externally validated only with a **signed evaluator receipt** binding the exact evaluation claim and evidence to a registered verifier key. Proposal, testing, validation and deployment are intentionally separate authority boundaries.

## Authority boundary

Genesis has no unrestricted host shell, credential harvesting, arbitrary third-party writes, autonomous payment, hidden persistence or uncontrolled replication. Browser interaction, MCP calls, processes and protocol methods exist only inside explicit configured capability and isolation boundaries.

More intelligence does not imply more authority. Homeostatic/resource pressure and Executive focus do not imply more authority either.

## What CI proves — and what it does not

The dependency-light lane verifies dependency consistency, compilation, focused Ruff correctness checks, installed-environment vulnerability audit, the full regression suite, zero-GPU Executive bootstrap/doctor, vital signs, runtime/supervisor and research smoke tests.

A separate sensorimotor lane installs real Chromium + MCP v2 and repeats vulnerability gating plus real BrowserContext interaction, in-process MCP client/server calls/resources, ELIA's MCP server port including Executive projection, DNS-pinned JSON-RPC round trips, process isolation readiness/timeouts, State Bus recovery, World Model verification boundaries, experience→future-context integration, Homeostasis/Metabolism and persistence/provider redaction tests.

CI does **not** prove absence of all vulnerabilities, phenomenal consciousness, AGI, causal understanding, economic self-sufficiency, indefinite survival or scientific uniqueness. Those remain stronger empirical claims and must remain falsifiable.

## Current frontier

The branch is a **Genesis 1.3 alpha candidate**: persistent identity + digital body + Sensorium/World/Causal state + verified vector metabolism + deterministic Executive attention + measured cognitive-energy feedback + hardened continuity/security boundaries.

The next external empirical gate remains a real repeated T4 lifecycle:

```text
wake → restore → Executive arbitration → Qwen cognition when justified
→ body action → observation → learning → hibernate
→ authenticated checkpoint → external relay → later wake
```

without human prompting each cycle, while measuring continuity, useful external value, resource use, cognitive cost, calibration, world-model quality, recovery behavior and drift over time.
