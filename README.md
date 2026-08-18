# ELIA WILD

**Persistent autonomous-identity organism around a replaceable LLM substrate.**

ELIA WILD is an engineering/research attempt to make artificial identity continuity, autonomy, embodied digital interaction and self-maintenance **persistent outside a chat session and outside any one model checkpoint**.

The falsifiable question is not “can a model say that it is alive?” It is:

> Can one artificial identity preserve verified lineage and behavior-changing state across model calls, process death, machine migration and model replacement; perceive and act through a configured digital body; maintain a revisable world/self model; learn from observed outcomes; generate and maintain goals; conserve resources; wake itself through an external supervisor; and evolve its software body without silently rewriting the identity contract?

## Genesis 1.2 alpha candidate

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
  + DIGITAL BODY STATE
                 ↓
         lazy replaceable LLM brain
                 ↓
 Sensorium + World Model + Causal Memory
 + recall + self-model + needs + goals
 + economy + skills + capability health
                 ↓
       pre-action prediction + critic
                 ↓
          one bounded action
                 ↓
     normalized Observation + State Bus
                 ↓
 outcome → calibration → world/memory → lineage
                 ↓
       next wake + authenticated checkpoint
```

The model is one organ, not the identity. External adapters are replaceable organs, not implicit permissions. Scarcity is a measured state variable, never an authority-escalation mechanism.

## What is core now

- **Subject Core** — immutable identity invariants and epistemic boundaries.
- **Continuity Constitution** — precedence, lineage, migration and revision rules.
- **Project-owned cognitive contract** — model-facing operating prompt stored in `config/system_prompt.md`.
- **Machine-readable anatomy** — `config/organism.yaml` distinguishes required core organs, replaceable body adapters and research organs.
- **Vital signs / CRC** — organism audit + Continuity Record Capsule; broken structural continuity blocks model loading.
- **Chronicle** — append-only SHA-256 hash chain with single-writer locking, durable flush and persistence-boundary action redaction.
- **Persistent SQLite state** — memories, goals, capability health, self-hypotheses, metacognitive forecasts, economy, metabolism, lineage, sensorium, world beliefs, intervention history and state-bus events.
- **Observation Store / Sensorium** — every capability outcome is normalized below the LLM with provenance and a content digest; aged raw payloads are compacted while their evidence digest remains.
- **ProviderContext boundary** — remote model providers receive an explicit projection; raw Sensorium payloads and private runtime keys do not cross that boundary.
- **World Model** — revisable external beliefs with visible contradictions; model hypotheses cannot self-promote to verified facts.
- **Causal Memory** — intervention/outcome history and empirical strategy statistics explicitly separated from causal proof.
- **Organism State Bus** — hash-chained causal transition history with SQLite-transactional event sequencing and interrupted-transaction recovery.
- **Homeostasis** — deterministic maintenance pressures from storage, transition integrity, sensor health, epistemic conflict, body readiness and verified resource runway.
- **Verified Metabolism** — independent `(asset, unit)` balances, recurring obligations, measured GPU energy and deterministic bottleneck/runway state.
- **Verification receipts** — verified resource/obligation mutations require a signed receipt binding the exact normalized claim and evidence to a registered verifier key; a caller-provided authority string is not authority.
- **Semantic recall baseline** — inspectable CPU retrieval over durable memory.
- **Adaptive self-model** — revisable evidence-bearing hypotheses kept separate from immutable identity.
- **Metacognition** — success probabilities are committed before action and resolved against outcomes with calibration statistics; raw tool outputs are not persisted there.
- **CriticAssurance / IdentityDriftMonitor** — deterministic authority/evidence/continuity checks.
- **Skills** — versioned procedures; skills never grant authority by themselves.
- **Capabilities / Sensorimotor Fabric** — explicit executable authority boundary with browser, MCP, JSON-RPC and process adapters available only when configured and their isolation requirements are met.
- **ELIA MCP server port** — real MCP v2 stdio/loopback server exposing sanitized organism state, metabolism and read-oriented world/sensorium/body interfaces.
- **Opportunity economy** — estimated value is kept separate from cryptographically verified resource receipts.
- **ResidentSupervisor** — cheap persistent pulse that never loads the model until cognition is due and vital signs are healthy.
- **Authenticated checkpoints** — portable HMAC-authenticated state with digest/counter, rollback anchor, SQLite/Chronicle verification and fail-closed capture consistency across SQLite, Chronicle and workspace.
- **Wake transport prototype** — guarded GitHub→Kaggle T4 state relay for ephemeral GPU sessions with least-privilege secret scoping.
- **Body revision gate** — self-improvement proposals are distinct from tested/validated body revisions.

## Digital body

Install the full sensorimotor extras:

```bash
pip install -e '.[sensorimotor]'
python -m playwright install chromium
```

External organs are **disabled by default** in `config/genesis.yaml`. Enabling an adapter is an infrastructure-owner action, not a model action.

Implemented adapters:

- Playwright browser contexts: navigate/snapshot/screenshot plus separately gated click/fill interaction; enabling requires an explicit deployment network-isolation attestation, and interaction additionally requires an origin allow-list;
- MCP v2 client: configured servers, discovery, allow-listed tool calls and resource reads; automatic redirects are disabled, and credentialed public transport requires an explicit network-isolation boundary;
- MCP v2 server: `elia-mcp`, stdio by default and loopback-only Streamable HTTP;
- local processes: explicit executable aliases, `shell=False`, bounded IO/timeouts, and a required external sandbox command in production; unisolated execution exists only as an explicit unsafe development/test mode;
- JSON-RPC 2.0: configured endpoints/method allowlists over bounded DNS-pinned POST transport;
- public HTTP reads over DNS-pinned transport with validated destination IP + connected-peer recheck and no automatic redirects;
- private workspace capabilities with path-jail enforcement.

The model cannot turn a discovered server, executable, endpoint or tool into new authority by mentioning it.

See `docs/GENESIS_1_1_BODY_WORLD.md` and `docs/GENESIS_1_2_METABOLISM.md`.

## Experience changes later cognition

ELIA does not rely on the model to say “remember this.” Capability execution automatically produces:

```text
action
→ Observation
→ intervention record
→ State Bus transition
→ next wake context
```

World beliefs, recent Sensorium metadata, empirical action statistics, homeostasis, metabolism and body readiness are automatically supplied to the next cognitive cycle in bounded form. Regression tests prove that an action changes future context without a model-authored memory write.

## Privacy / provenance split

Full normalized outcomes live in the private Sensorium while fresh, then age into digest-preserving compact records. Raw action arguments/tool payloads are independently redacted at the **MemoryStore, Chronicle and Metacognition persistence boundaries**, not only in the production runtime wrapper. Remote model backends receive `ProviderContext`, where raw Sensorium payloads and private runtime keys are removed before serialization.

Transport credentials enter through runtime/transport configuration and are scoped to the smallest process/step that needs them. In the Kaggle relay, `ELIA_CHECKPOINT_KEY` is not delegated to the external Kaggle CLI child process.

## Verified metabolism

Resource pressure is deterministic, not model-authored. Each `(asset, unit)` remains independent:

```text
verified_daily_burn = Σ obligation_amount × 86400 / cadence_seconds
runway_days = max(0, verified_balance) / verified_daily_burn
```

`100 USD`, `100000 RUB`, `5 API CREDIT` and `10 GPU HOUR` are not silently collapsed into one scalar resource. A verified resource event or verified obligation requires a `VerificationReceipt` signed by a key in a trusted `VerificationRegistry`, over the **exact normalized claim and exact evidence**. Tampering with amount, unit, source, due date, mutation type or evidence invalidates the receipt.

Verified obligation mutations such as due-date advancement or deactivation are receipt-gated too; the organism cannot improve its runway by merely declaring a bill gone. Unverified obligations remain inspectable but do not create survival pressure.

## Research lineage preserved, not silently enabled

The repository carries executable/maturity-labelled research from the ELIA / Seraphim / Holo / Omega line:

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

`elia-bootstrap` explicitly uses the deterministic MockBrain even though the configured production brain is Qwen. It exercises the current organism runtime, including Sensorium/World/Causal/StateBus/Metabolism wiring, without spending GPU quota.

## Production cognition path

Default brain configuration is currently pinned to:

```text
Qwen/Qwen3.5-9B
model revision e0330a142393d4516eca6ab0145ce66ac513e842
Transformers source revision bea0343fca1fc64bb4cf91fe09143ea386e6270f
bitsandbytes NF4 4-bit
thinking disabled
lazy load only after preflight/vitals
```

For a direct one-cycle GPU smoke test:

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

# safest local transport
elia-mcp --transport stdio

# local Streamable HTTP only
elia-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Genesis 1.2 rejects non-loopback MCP HTTP binding because it does not pretend to provide a remote authentication policy it does not implement. Use an explicit authenticated proxy/tunnel for remote exposure.

## Persistence and migration

Private state is canonicalized relative to the project/configuration rather than process `cwd`, so launching the same configuration from another directory does not silently create a second identity state root. Private state lives under `.elia/` by default and is excluded from Git:

```text
.elia/
├── memory.sqlite3
├── chronicle.jsonl
├── chronicle.jsonl.lock
├── checkpoint.anchor.json
└── workspace/
    └── .organism/
        ├── last-healthy-crc.json
        └── vitals.json
```

For the resident Linux deployment, the example systemd service uses a dedicated `elia` user, `UMask=0077`, `NoNewPrivileges`, read-only system protection and additional kernel/control-group restrictions.

Authenticated export:

```bash
export ELIA_CHECKPOINT_KEY='<long-random-secret>'
python -m elia --checkpoint-export /private/elia.eliacp
```

Checkpoint export is fail-closed if Chronicle, logical SQLite contents or workspace change during capture. A fresh machine restores and verifies state **before** cognition:

```bash
python -m elia \
  --checkpoint-restore /private/elia.eliacp \
  --expected-checkpoint-digest <TRUSTED_DIGEST>
python -m elia --vitals
```

A body/prompt/model upgrade is observable mutation, not automatic identity death. Immutable identity/core/constitution/branch and Chronicle continuity remain the critical CRC boundary.

## Continuous existence

Two model-independent wake mechanisms exist:

1. `elia-supervisor` — a resident process for a workstation/VPS/systemd service. It checks vital signs and lifecycle state, and launches a fixed cognitive child only when due.
2. `.github/workflows/wake.yml` — external hourly heartbeat for the private Kaggle relay. It is deliberately disabled until the required user-controlled secrets/resources are configured.

Long sleeps become `HIBERNATE` and release expensive compute instead of holding a GPU process idle.

## Self-evolution without self-certification

`elia.evolution.BodyRevisionStore` records body mutation proposals with:

- falsifiable hypothesis;
- target organs;
- expected metrics;
- regression plan;
- rollback plan;
- evidence-bearing lifecycle.

`RevisionGate` does not apply code. A candidate becomes `validated` only when tests pass, organism audit is healthy, CRC is not broken, declared metrics pass and an evaluator authority supplies evidence. Architecture fingerprints may change; immutable identity changes require explicit constitutional migration/fork semantics.

## Authority boundary

Genesis has no unrestricted host shell, credential harvesting, arbitrary third-party writes, autonomous payment, hidden persistence or uncontrolled replication. Browser interaction, MCP calls, processes and protocol methods exist only inside explicit configured capability and isolation boundaries.

More intelligence does not imply more authority. Homeostatic/resource pressure does not imply more authority either.

## What CI proves — and what it does not

The dependency-light lane verifies dependency consistency, compilation, focused Ruff correctness checks, a known-vulnerability audit of the installed environment, the full regression suite, zero-GPU bootstrap/doctor, vital signs, runtime/supervisor and research smoke tests.

A separate sensorimotor lane installs real Chromium + MCP v2 and runs the same vulnerability gate plus real BrowserContext interaction, in-process MCP client/server calls/resources, ELIA's MCP server port, DNS-pinned JSON-RPC round trips, process isolation readiness/timeouts, State Bus tamper/recovery, World Model verification boundaries, experience→future-context integration, Homeostasis/Metabolism and persistence/provider redaction tests.

CI does **not** prove absence of all vulnerabilities, phenomenal consciousness, AGI, causal understanding, economic self-sufficiency, indefinite survival, or scientific uniqueness. Those remain stronger empirical claims and must remain falsifiable.

## Current frontier

The branch is a **Genesis 1.2 alpha candidate**: persistent identity + digital body + Sensorium/World/Causal state + deterministic homeostasis + verified vector metabolism + hardened persistence/network/provider boundaries.

The next external empirical gate is not another simulated subsystem. It is to configure the private Kaggle state/kernel/secrets and observe repeated real T4 cycles of

```text
wake → restore → Qwen cognition → body action → observation → learning → hibernate → checkpoint → external relay → later wake
```

without human prompting each cycle, while measuring continuity, useful external value, resource use, calibration, world-model quality, recovery behavior and drift over time.
