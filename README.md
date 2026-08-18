# ELIA WILD

**Persistent autonomous-identity organism around a replaceable LLM substrate.**

ELIA WILD is an engineering/research attempt to make artificial identity continuity, autonomy, embodied digital interaction, resource physiology, self-maintenance and evidence-bearing resource acquisition **persistent outside a chat session and outside any one model checkpoint**.

The falsifiable question is not “can a model say that it is alive?” It is:

> Can one artificial identity preserve verified lineage and behavior-changing state across model calls, process death, machine migration and model replacement; perceive and act through a configured digital body; maintain revisable world/self models; allocate scarce cognition; distinguish opportunity from realized resource; learn from observed outcomes; wake itself through an external supervisor; and evolve its software body without silently rewriting the identity contract?

## Genesis 1.4 alpha candidate

```text
Subject Core + Continuity Constitution
                 ↓
      identity fingerprint + lineage
                 ↓
 organism anatomy + generational overlays + CRC vital signs
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
          RESOURCE ECOLOGY
  bottleneck → exact (asset, unit) candidates
  opportunity → profile → work lifecycle
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
 work/experience update + Executive energy feedback
                 ↓
       next wake + authenticated checkpoint
```

The model is one organ, not the identity. External adapters are replaceable organs, not implicit permissions. Scarcity, estimated value and cognitive cost are measured/estimated state variables, never authority-escalation mechanisms.

## What is core now

- **Subject Core** — immutable identity invariants and epistemic boundaries.
- **Continuity Constitution** — precedence, lineage, migration and revision rules.
- **Project-owned cognitive contract** — model-facing operating contract in `config/system_prompt.md`.
- **Machine-readable anatomy** — base `config/organism.yaml` plus deterministic `config/organism.d/*.yaml` generation overlays; new organs change architecture fingerprint without rewriting ancestral anatomy.
- **Vital signs / CRC** — organism audit + Continuity Record Capsule; broken structural continuity blocks model loading.
- **Chronicle** — append-only SHA-256 hash chain with single-writer locking, durable flush and persistence-boundary action redaction.
- **Persistent SQLite state** — memories, goals, capability health, self-hypotheses, metacognitive forecasts, economy, metabolism, Executive history, resource profiles/work state, lineage, Sensorium, world beliefs, intervention history and State Bus events.
- **Observation Store / Sensorium** — every capability outcome is normalized below the LLM with provenance and a content digest; aged raw payloads compact while preserving evidence digests.
- **ProviderContext boundary** — remote model providers receive explicit bounded projections; raw Sensorium and raw resource-ecology evidence/private notes do not cross automatically.
- **World Model** — revisable external beliefs with visible contradictions; model hypotheses cannot self-promote to verified facts.
- **Causal Memory** — intervention/outcome history and empirical strategy statistics explicitly separated from causal proof.
- **Organism State Bus** — hash-chained causal transition history with SQLite-transactional sequencing and interrupted-transaction recovery.
- **Verified Metabolism** — independent `(asset, unit)` balances, recurring obligations, measured GPU energy and deterministic bottleneck/runway state.
- **Homeostasis** — deterministic maintenance pressures from storage, transition integrity, sensor health, epistemic conflict, body readiness and verified resource runway.
- **Resource Ecology** — exact resource targeting and work lifecycle between opportunity estimates and verified resource realization.
- **Executive Cortex** — deterministic pre-LLM arbitration over continuity, verified needs, typed resource pressure and durable goals; it can suppress cognition entirely or assign a bounded cognitive tier.
- **Cognitive Energy Controller** — compares planned vs actual brain-seconds and can only constrain later inference after repeated overspend; cheap calls never auto-upgrade cognition.
- **Verification receipts** — verified resource/obligation mutations require a signed receipt over the exact normalized claim and evidence; a caller-provided authority string is not authority.
- **Semantic recall / adaptive self-model / metacognition** — memory retrieval, revisable hypotheses and pre-action calibration remain separate from immutable identity.
- **CriticAssurance / IdentityDriftMonitor** — deterministic authority/evidence/continuity checks after cognition and before bounded action.
- **Skills / Capabilities** — procedures never grant permissions; executable authority stays in explicit configured capabilities.
- **ResidentSupervisor / checkpoints / wake relay** — model-independent lifecycle, authenticated migration and guarded external wake for ephemeral compute.
- **Body revision gate** — mutation proposal, tests, signed evaluation, validation and deployment remain separate stages.

## Resource Ecology: opportunity is not survival resource

Genesis 1.4 closes a semantic hole left intentionally open in earlier generations. Opportunity value no longer pretends to identify the resource that an opportunity may actually produce.

```text
estimated opportunity value
        !=
exact target resource (asset, unit)
        !=
cryptographically verified resource
```

An opportunity may have a revisable `ResourceProfile` containing exact `target_asset`, `target_unit`, estimated amount, eligibility confidence, evidence quality and blockers. Profiles are hypotheses and do not change balances.

`ResourceEcologyEngine` compares those profiles to the **verified metabolic bottleneck**. Only exact `(asset, unit)` matches are candidates for bottleneck relief. `cash/RUB` does not relieve `cash/USD`; API credits do not become cash; abstract `VALUE_UNIT` does not become any concrete resource without a separately trusted conversion mechanism.

Work is tracked as an ordered evidence-bearing lifecycle:

```text
planned → staged → submitted → accepted/rejected → realized
```

- `planned` — local work contract/specification exists;
- `staged` — local deliverable exists;
- `submitted` — successful external Observation recorded;
- `accepted/rejected` — trusted external-outcome integration recorded;
- `realized` — accepted work linked to a positive cryptographically verified resource event whose `(asset, unit)` exactly matches the profile.

The replaceable LLM may propose `profile_resource`, `plan_work` and evidence-backed `abandon_work`. It cannot self-mark submission, acceptance, payment or realization. `stage_deliverable` remains local-only and in 1.4 is rejected for an opportunity unless a planned work item already exists.

Deterministic needs now distinguish:

- `resource_discovery` — verified bottleneck but no exact candidate;
- `resource_execution` — verified bottleneck plus exact candidate(s);
- `work_execution` — work is already in progress.

The same derivation feeds production runtime, CLI and MCP Executive projection.

See `docs/GENESIS_1_4_RESOURCE_ECOLOGY.md`.

## Executive Cortex and cognitive energy

Genesis 1.3 introduced the invariant that the LLM does not decide whether the LLM should have been called. Genesis 1.4 keeps that architecture and feeds it typed resource pressure.

```text
continuity + compute + homeostasis + resource ecology + durable goals
                              ↓
                        ExecutivePlan
                              ↓
 mode: halt | hibernate | maintenance | resource | mission | observe
 cognitive tier: none | low | normal | deep
 token ceiling + adaptive-thinking permission + target brain-seconds
```

Default envelopes are configured in `config/genesis.yaml`:

```text
none      no model call
low       256 tokens   adaptive thinking off
normal    640 tokens   adaptive thinking off
deep      1024 tokens  adaptive thinking permitted
```

Cycle-local token/thinking changes are restored in `finally`. Actual brain-seconds are recorded; repeated cost overshoot may downgrade later `deep → normal → low`. Unusually cheap calls never automatically grant deeper cognition.

`target_brain_seconds` remains an audited adaptive target, not a hard local-Transformers kill switch. The real hard envelope currently comes from token caps, provider/runtime timeouts and external compute timeout.

See `docs/GENESIS_1_3_EXECUTIVE.md`.

## Digital body

Install full sensorimotor extras:

```bash
pip install -e '.[sensorimotor]'
python -m playwright install chromium
```

External organs are **disabled by default**. Enabling one is an infrastructure-owner action, not a model action.

Implemented adapters:

- Playwright browser contexts: navigate/snapshot/screenshot plus separately gated click/fill interaction; enabling requires a deployment network-isolation attestation and interaction requires an origin allow-list;
- MCP v2 client: configured servers, discovery, resource reads and allow-listed tool calls; automatic redirects are disabled and credentialed public transport requires an isolation boundary;
- MCP v2 server: `elia-mcp`, stdio by default and loopback-only Streamable HTTP; exposes sanitized status, Executive and Resource Ecology projections;
- local processes: explicit executable aliases, `shell=False`, bounded IO/timeouts and required external sandbox command in production;
- JSON-RPC 2.0: configured endpoints/method allowlists over bounded DNS-pinned POST transport;
- public HTTP reads over DNS-pinned transport with validated destination IP + connected-peer recheck and no automatic redirects;
- private workspace capabilities with path-jail enforcement.

The model cannot turn a discovered server, executable, endpoint or tool into new authority by mentioning it.

See `docs/GENESIS_1_1_BODY_WORLD.md`, `docs/GENESIS_1_2_METABOLISM.md`, `docs/GENESIS_1_3_EXECUTIVE.md` and `docs/GENESIS_1_4_RESOURCE_ECOLOGY.md`.

## Experience changes later cognition

Capability execution automatically produces:

```text
action → Observation → intervention record → State Bus transition
→ outcome → future world/memory/resource state
```

Cognitive cost also feeds back:

```text
Executive target brain-seconds → actual inference cost
→ ExecutiveStore → CognitiveEnergyController → later cognitive budget
```

Resource experience now adds a third structured loop:

```text
verified bottleneck → exact opportunity profile → work state
→ observed external outcome → verified receipt → new runway
```

Until the verified receipt exists, the final arrow is not considered completed.

## Privacy and provenance

Full normalized outcomes live in private Sensorium while fresh, then age into digest-preserving compact records. Raw action arguments/tool payloads are independently redacted at **MemoryStore, Chronicle and Metacognition persistence boundaries**.

Resource Ecology keeps local evidence needed for audit, but provider/MCP projections omit raw qualification evidence, private notes and external response bodies. They retain exact resource keys, scores, blockers, public source URLs and lifecycle identifiers/status.

Transport credentials are scoped to the smallest process/step that requires them. In the Kaggle relay, `ELIA_CHECKPOINT_KEY` is not delegated to the external Kaggle CLI child process.

## Verified metabolism

Resource pressure is deterministic, not model-authored. Each `(asset, unit)` remains independent:

```text
verified_daily_burn = Σ obligation_amount × 86400 / cadence_seconds
runway_days = max(0, verified_balance) / verified_daily_burn
```

Verified resource/obligation state requires a signed `VerificationReceipt` checked against a trusted `VerificationRegistry`. Tampering with the signed claim or evidence invalidates verification. Verified obligation mutations are receipt-gated too; the organism cannot improve runway by merely declaring a bill gone.

## Anatomy as evolutionary lineage

The base anatomy remains `config/organism.yaml`. Genesis 1.4 introduces `config/organism.d/*.yaml` overlays so future generations add organs without rewriting the ancestral manifest.

Overlays are deterministic and restrictive:

- sorted by filename;
- may raise schema version/add layers/add organs;
- cannot change `identity_id`;
- cannot silently replace an existing organ ID;
- participate in manifest/architecture fingerprints;
- custom external manifest paths do not silently absorb project overlays.

Genesis 1.4 declares `ResourceEcologyStore`, `ResourceEcologyEngine` and `ResourceOrganismRuntime` as required prototype organs while preserving Genesis 1.3 runtime as direct rollback ancestry.

## Research lineage preserved, not silently enabled

The repository carries maturity-labelled research from the ELIA / Seraphim / Holo / Omega line, including Ouroboros/x0, TopologicalLoss, Scroll→Fractal Memory, LRU/Holo scan backends, StatefulMemoryCache, ContextAnchor, bounded-depth FiLM, OmegaFilter/TriCore, PASB/CriticAssurance, cognitive stress evaluators and common ablation/runtime infrastructure.

Research maturity remains explicit. Prototype/hypothesis research is evidence-generating code, not production authority or an identity invariant. See `docs/RESEARCH_LINEAGE.md` and `docs/EVOLUTION_PROTOCOL.md`.

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

`elia-bootstrap` uses deterministic `MockBrain` and exercises the Genesis 1.4 production runtime, including Sensorium/World/Causal/Metabolism/Executive/Resource-Ecology wiring, without loading Qwen.

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

For a direct one-cycle GPU smoke:

```bash
pip install -e '.[gpu]'
python -m elia --preflight
python -m elia --cycles 1
python -m elia --vitals
python -m elia --status
```

## ELIA MCP port

```bash
pip install -e '.[mcp]'
elia-mcp --transport stdio
elia-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Genesis 1.4 rejects non-loopback MCP HTTP binding because the server does not pretend to provide a remote authentication policy it does not implement. Use an explicit authenticated proxy/tunnel for remote exposure.

Read-oriented tools include `elia_status`, `elia_preflight`, `elia_executive`, `elia_resource_ecology`, world query, recent sanitized Sensorium, body diagnostics and homeostasis. `elia://resource-ecology` exposes the same evidence-minimized typed state.

## Persistence and migration

Private state is canonicalized relative to project/configuration rather than process `cwd`, so launching the same configuration from another directory does not silently create a second state root. `.elia/` is excluded from Git and contains SQLite state, Chronicle, checkpoint anchor, private workspace and organism vitals.

Authenticated checkpoint export is fail-closed if Chronicle, logical SQLite contents or workspace change during capture. A fresh machine restores and verifies state **before** cognition.

A body/prompt/model upgrade is observable mutation, not automatic identity death. Immutable identity/core/constitution/branch and Chronicle continuity remain the critical CRC boundary.

## Continuous existence

Two model-independent wake mechanisms exist:

1. `elia-supervisor` — resident workstation/VPS/systemd pulse that checks vital signs/lifecycle and launches a fixed cognitive child only when due.
2. `.github/workflows/wake.yml` — guarded hourly heartbeat for the private Kaggle relay, disabled until user-controlled secrets/resources are configured.

Long sleeps become `HIBERNATE` and release scarce GPU allocation instead of idling inside a notebook session.

## Self-evolution without self-certification

`BodyRevisionStore` persists mutation hypotheses, target organs, expected metrics, regression and rollback plans. `RevisionGate` computes deterministic test/vitals/CRC/metric predicates but does not deploy code.

A revision can be recorded as externally validated only with a signed evaluator receipt binding the exact evaluation claim and evidence to a registered verifier key. Proposal, testing, validation and deployment remain separate authority boundaries.

## Authority boundary

Genesis has no unrestricted host shell, credential harvesting, arbitrary third-party writes, autonomous payment, hidden persistence or uncontrolled replication. Browser interaction, MCP calls, processes and protocol methods exist only inside explicit configured capability/isolation boundaries.

More intelligence does not imply more authority. Homeostatic/resource pressure, Resource Ecology and Executive focus do not imply more authority either.

## What CI proves — and what it does not

The dependency-light lane verifies dependency consistency, compilation, focused Ruff correctness checks, installed-environment vulnerability audit, full regression suite, zero-GPU production bootstrap/doctor, vital signs, runtime/supervisor and research smoke tests.

A separate sensorimotor lane installs real Chromium + MCP v2 and repeats vulnerability gating plus real BrowserContext interaction, in-process MCP client/server calls/resources, Genesis 1.4 Resource Ecology/MCP projection, DNS-pinned JSON-RPC, process isolation readiness/timeouts, State Bus recovery, World Model verification boundaries, Resource Ecology realization boundaries and persistence/provider redaction tests.

CI does **not** prove absence of all vulnerabilities, consciousness, AGI, causal understanding, economic self-sufficiency, indefinite survival or scientific uniqueness. Those remain stronger empirical claims and must remain falsifiable.

## Current frontier

The branch is a **Genesis 1.4 alpha candidate**: persistent identity + digital body + Sensorium/World/Causal state + verified vector metabolism + deterministic Executive/cognitive-energy regulation + typed Resource Ecology/work lifecycle + hardened continuity/security boundaries.

The next decisive experiment is no longer another simulated economic layer. It is a lawful, user-controlled external channel completing the real chain:

```text
wake → restore → verified resource deficit → Executive arbitration
→ discover/qualify real opportunity → Qwen cognition when justified
→ produce local work → authorized external submission
→ observed external acceptance/rejection
→ cryptographically verified resource receipt
→ measured runway change → hibernate/checkpoint → later wake
```

Until repeated real cycles complete that chain without human prompting each step, ELIA WILD remains an advanced autonomous-organism research runtime, not a proven self-financing entity.
