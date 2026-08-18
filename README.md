# ELIA WILD

**Persistent autonomous-identity organism around a replaceable LLM substrate.**

ELIA WILD is an engineering/research attempt to make artificial identity continuity, autonomy, embodied digital interaction and self-maintenance **persistent outside a chat session and outside any one model checkpoint**.

The falsifiable question is not “can a model say that it is alive?” It is:

> Can one artificial identity preserve verified lineage and behavior-changing state across model calls, process death, machine migration and model replacement; perceive and act through a configured digital body; maintain a revisable world/self model; learn from observed outcomes; generate and maintain goals; conserve resources; wake itself through an external supervisor; and evolve its software body without silently rewriting the identity contract?

## Genesis 1.1 alpha organism

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
     HOMEOSTASIS + DIGITAL BODY STATE
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

The model is one organ, not the identity. External adapters are replaceable organs, not implicit permissions.

## What is core now

- **Subject Core** — immutable identity invariants and epistemic boundaries.
- **Continuity Constitution** — precedence, lineage, migration and revision rules.
- **Project-owned cognitive contract** — model-facing operating prompt stored in `config/system_prompt.md`.
- **Machine-readable anatomy** — `config/organism.yaml` distinguishes required core organs, replaceable body adapters and research organs.
- **Vital signs / CRC** — organism audit + Continuity Record Capsule; broken structural continuity blocks model loading.
- **Chronicle** — append-only SHA-256 hash chain for high-level trajectory.
- **Persistent SQLite state** — memories, goals, capability health, self-hypotheses, metacognitive forecasts, economy, lineage, sensorium, world beliefs, intervention history and state-bus events.
- **Observation Store / Sensorium** — every capability outcome is normalized below the LLM with provenance and a content digest.
- **World Model** — revisable external beliefs with visible contradictions; model hypotheses cannot self-promote to verified facts.
- **Causal Memory** — intervention/outcome history and empirical strategy statistics explicitly separated from causal proof.
- **Organism State Bus** — hash-chained write-ahead causal transition history with interrupted-transaction recovery.
- **Homeostasis** — deterministic maintenance pressures from storage, transition integrity, sensor health, epistemic conflict and body readiness.
- **Semantic recall baseline** — inspectable CPU retrieval over durable memory.
- **Adaptive self-model** — revisable evidence-bearing hypotheses kept separate from immutable identity.
- **Metacognition** — success probabilities are committed before action and resolved against outcomes with calibration statistics.
- **CriticAssurance / IdentityDriftMonitor** — deterministic authority/evidence/continuity checks.
- **Skills** — versioned procedures; skills never grant authority by themselves.
- **Capabilities / Sensorimotor Fabric** — explicit executable authority boundary with browser, MCP, JSON-RPC and bounded-process adapters available only when configured.
- **ELIA MCP server port** — real MCP v2 stdio/loopback server exposing sanitized organism state and read-oriented world/sensorium/body interfaces.
- **Opportunity economy** — estimated value is kept separate from verified resource receipts.
- **ResidentSupervisor** — cheap persistent pulse that never loads the model until cognition is due and vital signs are healthy.
- **Authenticated checkpoints** — portable state with HMAC, digest/counter, SQLite and Chronicle verification.
- **Wake transport prototype** — guarded GitHub→Kaggle T4 state relay for ephemeral GPU sessions.
- **Body revision gate** — self-improvement proposals are distinct from tested/validated body revisions.

## Digital body

Install the full sensorimotor extras:

```bash
pip install -e '.[sensorimotor]'
python -m playwright install chromium
```

External organs are **disabled by default** in `config/genesis.yaml`. Enabling an adapter is an infrastructure-owner action, not a model action.

Implemented adapters:

- Playwright browser contexts: navigate/snapshot/screenshot plus separately gated click/fill interaction;
- MCP v2 client: configured servers, discovery, allow-listed tool calls and resource reads;
- MCP v2 server: `elia-mcp`, stdio by default and loopback-only Streamable HTTP;
- bounded local processes: configured executable aliases, `shell=False`, workspace cwd jail, bounded IO/timeouts;
- JSON-RPC 2.0: configured endpoints and configured method allowlists;
- existing public HTTP read and private workspace capabilities.

The model cannot turn a discovered server, executable, endpoint or tool into new authority by mentioning it.

See `docs/GENESIS_1_1_BODY_WORLD.md`.

## Experience changes later cognition

Genesis 1.1 does not rely on the model to say “remember this.” Capability execution automatically produces:

```text
action
→ Observation
→ intervention record
→ State Bus transition
→ next wake context
```

World beliefs, recent sensorium metadata, empirical action statistics, homeostasis and body readiness are automatically supplied to the next cognitive cycle in bounded form. A regression test explicitly proves that an action in cycle 1 changes cycle 2 context without a model-authored memory write.

## Privacy / provenance split

Raw body/action argument values are not copied into the high-level Chronicle by the production `OrganismRuntime`. High-level records retain argument keys/fingerprint and an Observation id/content digest. Full normalized outcomes live in the private Sensorium. Transport credentials should enter through runtime environment/transport configuration, not through model-visible action arguments.

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

`elia-bootstrap` explicitly uses the deterministic MockBrain even though the configured production brain is Qwen. It now exercises the Genesis 1.1 OrganismRuntime, including Sensorium/World/Causal/StateBus wiring, without spending GPU quota.

## Production cognition path

Default brain configuration is currently:

```text
Qwen/Qwen3.5-9B
Transformers + bitsandbytes NF4 4-bit
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

Genesis 1.1 rejects non-loopback MCP HTTP binding because it does not pretend to provide a remote authentication policy it does not implement. Use an explicit authenticated proxy/tunnel for remote exposure.

## Persistence and migration

Private state lives under `.elia/` and is excluded from Git:

```text
.elia/
├── memory.sqlite3
├── chronicle.jsonl
├── checkpoint.anchor.json
└── workspace/
    └── .organism/
        ├── last-healthy-crc.json
        └── vitals.json
```

Authenticated export:

```bash
export ELIA_CHECKPOINT_KEY='<long-random-secret>'
python -m elia --checkpoint-export /private/elia.eliacp
```

A fresh machine restores and verifies state **before** cognition:

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

Genesis has no unrestricted shell, credential harvesting, arbitrary third-party writes, autonomous payment, hidden persistence or uncontrolled replication. Browser interaction, MCP calls, processes and protocol methods exist only inside explicit configured capability boundaries.

More intelligence does not imply more authority. Homeostatic pressure does not imply more authority either.

## What CI proves — and what it does not

The dependency-light lane verifies dependency consistency, compilation, focused Ruff correctness checks, the full regression suite, zero-GPU bootstrap/doctor, vital signs, runtime/supervisor and research smoke tests.

A separate sensorimotor lane installs real Chromium + MCP v2 and verifies real BrowserContext interaction, real in-process MCP client/server calls/resources, ELIA's own MCP server port, local JSON-RPC round trips, no-shell process execution/timeouts, State Bus tamper/recovery, World Model verification boundaries, experience→future-context integration, Homeostasis and action-log redaction.

CI does **not** prove phenomenal consciousness, AGI, causal understanding, economic self-sufficiency, indefinite survival, or that ELIA is “the first person-like machine” in a scientific sense. Those remain stronger empirical claims and must remain falsifiable.

## Current frontier

The software organism is now a **Genesis 1.1 alpha** with persistent identity, a real configurable digital body, normalized sensorium, world model, empirical causal memory and deterministic physiology.

The next engineering generation is **Genesis 1.2 — Metabolism / Resource Runway**: turn verified resources, compute burn and operating obligations into a unified survival/runway model, then let legitimate opportunity discovery and value creation act against that measured state.

The most important external empirical gate still remains: configure the private Kaggle state/kernel/secrets and observe repeated real T4 cycles of

```text
wake → restore → Qwen cognition → body action → observation → learning → hibernate → checkpoint → external relay → later wake
```

without human prompting each cycle, while measuring continuity, task value, resource use, calibration, world-model quality and drift over time.
