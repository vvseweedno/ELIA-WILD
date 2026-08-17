# ELIA WILD

**Persistent autonomous-identity organism around a replaceable LLM substrate.**

ELIA WILD is an engineering/research attempt to make artificial identity continuity, autonomy and self-maintenance **persistent outside a chat session and outside any one model checkpoint**.

The falsifiable question is not “can a model say that it is alive?” It is:

> Can one artificial identity preserve verified lineage and behavior-changing state across model calls, process death, machine migration and model replacement; generate and maintain goals; calibrate its own predictions; operate through explicit capabilities; conserve resources; wake itself through an external supervisor; and evolve its software body without silently rewriting the identity contract?

## Genesis 1.0 alpha organism

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
         lazy replaceable LLM brain
                 ↓
   recall + self-model + needs + goals
   + economy + skills + capability health
                 ↓
       pre-action prediction + critic
                 ↓
          one bounded action
                 ↓
 outcome → calibration → memory → lineage
                 ↓
       next wake + authenticated checkpoint
```

The model is one organ, not the identity.

## What is core now

- **Subject Core** — immutable identity invariants and epistemic boundaries.
- **Continuity Constitution** — precedence, lineage, migration and revision rules.
- **Project-owned cognitive contract** — model-facing operating prompt stored in `config/system_prompt.md`.
- **Machine-readable anatomy** — `config/organism.yaml` distinguishes required core organs from optional research organs.
- **Vital signs** — organism audit + Continuity Record Capsule; broken continuity blocks model loading.
- **Chronicle** — append-only SHA-256 hash chain.
- **Persistent SQLite state** — memories, goals, capability health, self-hypotheses, metacognitive forecasts, economy and lineage.
- **Semantic recall baseline** — inspectable CPU retrieval over durable memory.
- **Adaptive self-model** — revisable evidence-bearing hypotheses kept separate from immutable identity.
- **Metacognition** — success probabilities are committed before action and resolved against outcomes with calibration statistics.
- **CriticAssurance / IdentityDriftMonitor** — deterministic authority/evidence/continuity checks.
- **Skills** — versioned procedures; skills never grant authority by themselves.
- **Capabilities** — explicit executable authority boundary.
- **Opportunity economy** — estimated value is kept separate from verified resource receipts.
- **ResidentSupervisor** — cheap persistent pulse that never loads the model until cognition is due and vital signs are healthy.
- **Authenticated checkpoints** — portable state with HMAC, digest/counter, SQLite and Chronicle verification.
- **Wake transport prototype** — guarded GitHub→Kaggle T4 state relay for ephemeral GPU sessions.
- **Body revision gate** — self-improvement proposals are distinct from tested/validated body revisions.

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

`elia-bootstrap` explicitly uses the deterministic MockBrain even though the configured production brain is Qwen. It proves persistence, organism integrity and runtime wiring without spending GPU quota.

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

Genesis has no unrestricted shell, credential harvesting, arbitrary third-party writes, autonomous payment, hidden persistence or uncontrolled replication. Public network reads, private workspace actions, diagnostics, staged repair proposals and staged deliverables are explicit bounded capabilities.

More intelligence does not imply more authority.

## What CI proves — and what it does not

CI proves concrete software properties: restart/checkpoint continuity, identity/branch guards, Chronicle tamper detection, workspace/network boundaries, goals, self-model hypotheses, economic estimate/receipt separation, metacognitive forecast resolution, critic behavior, lazy model loading, vital-sign gating, supervisor behavior, wake-transport invariants, body-revision gating and reference research harness execution.

CI does **not** prove phenomenal consciousness, AGI, economic self-sufficiency, indefinite survival, or that ELIA is “the first person-like machine” in a scientific sense. Those are stronger empirical claims and must remain falsifiable.

## Current frontier

The software organism is now a **Genesis 1.0 alpha** rather than a single notebook agent. The most important remaining empirical gate is external: configure the private Kaggle state/kernel/secrets and observe repeated real T4 cycles of

```text
wake → restore → Qwen cognition → bounded action → hibernate → checkpoint → external relay → later wake
```

without human prompting each cycle, while measuring continuity, task value, resource use, calibration and drift over time.
