# ELIA Research Lineage

This document prevents two opposite failures: losing the experimental history that shaped ELIA, and silently promoting old notebooks into production truth.

Every research component is assigned one maturity class:

- **proven** — implemented and reproducibly validated within a stated scope;
- **prototype** — executable or specified well enough to test, but not established as a general improvement;
- **archived** — retained for provenance, negative results and failure analysis; not active by default;
- **hypothesis** — a research direction awaiting implementation or convincing evidence.

The machine-readable counterpart is `elia/research/registry.py`.

## 1. Seraphim / ELIA plugin line

The canonical architecture is a **HuggingFace-compatible adapter over existing LLMs**, not a new foundation model from scratch.

### Ouroboros / x0 hidden-state injection — prototype

`elia/research/seraphim.py::ouroboros_inject`

A stable/anchored representation `x0` is injected through depth using an explicit `DecaySchedule`. The repository preserves silver-ratio, 0.5, learned-rho and octagonal schedules as ablations. Genesis does not enable hidden-state mutation automatically; any integration must identify the target model layer boundary and pass continuity + task ablations.

### TopologicalLoss — prototype

`elia/research/seraphim.py::topological_loss`

The current repository provides an explicit topology-preservation surrogate based on normalized pairwise geometry and stronger nearest-neighbor preservation. It is deliberately documented as the **current reference implementation**, not falsely claimed to be byte-for-byte identical to every historical notebook objective.

### ScrollMemory → FractalMemory — prototype

`elia/research/memory.py`

`ScrollMemory` is the small bounded chronological baseline. `FractalMemory` implements the retained idea of surprisal-gated writes across progressively coarser retention levels. The write gate can be driven by token NLL through `surprisal_from_token_loss`.

### IdentityDriftMonitor — implemented in organism core

`elia/assurance.py::IdentityDriftMonitor`

The production monitor is **structural rather than lexical**. Changing model backend or wording is not automatically identity loss. Subject Core fingerprint mutation, lost core commitments, unresolved lineage, identity-id mutation or backward state are high-weight failures.

### PASB / CriticAssurance — implemented in organism core

`elia/assurance.py::CriticAssurance`

The critic is a deterministic pre-action gate. Unknown/disabled authority and identity-continuity contradictions hard-reject external action. Recoverable validation errors (for example unsupported terminal goal claims) are left to specialized validators so exact failure evidence is preserved.

### HybridOptimizer — hypothesis

The repository currently exposes an explicit `hybrid_objective` combiner rather than pretending an unvalidated optimizer is required. A future optimizer wrapper must be benchmarked separately from objective changes.

## 2. MemoryBackend / Holo / LRU line

Genesis preserves a common research interface with four names:

```text
scroll
fractal
lru_scan
holo_scan
```

### LRU scan — prototype and important baseline

The reference recurrence is:

```text
h_t = f_t h_(t-1) + (1-f_t) x_t
```

It is represented as an associative affine transform, allowing the same algebra to map to parallel associative-scan implementations later.

Historical archived 10k enwiki8 runs recorded approximately:

```text
Holo validation BPB ≈ 2.0474
LRU validation BPB  ≈ 1.6257
```

The saved LRU run was also materially faster. These numbers are preserved because they changed our architecture decision: **LRU remains a mandatory baseline; full Holo is not default core.** They are not presented as a universal benchmark across modern implementations.

### Holo scan — prototype

The current `holo_scan` is a small complex/phase affine recurrence that preserves the reusable Holo idea without importing the historical full byte-level model into Genesis.

### Full complex Holo model — archived

Historical Holo components included complex embeddings/state, ComplexRMSNorm, phase-preserving projection, HoloAttention/HoloMemory and associative scan. They remain research material.

The `holo-s-336` TPU/JAX notebook series is explicitly **failure-analysis evidence**, with failures including TPU reshape issues, pyarrow incompatibility, invalid JAX precision configuration, removed Flax APIs, pmap misuse, gated/unsupported datasets and memory/runtime limits. These attempts are not performance proof.

A later HoloSeraphim/TinyStories TPU notebook appears to have completed about 10k steps around loss `2.1759` at roughly `23k tok/s`; because it lacks a validated comparable baseline, it remains archived evidence rather than a winning benchmark claim.

### Octagonal decay — prototype ablation

Historical schedule:

```text
[sr², sr², sr, sr, 1-sr, 1-sr, 1, 1]
```

where `sr = sqrt(2)-1`. It is implemented as `DecaySchedule("octagonal")`.

### Later complex adapters — hypothesis

- ComplexRMSNorm
- phase-preserving `[Re; Im]` projection
- polar-vs-Cartesian embedding ablation

These remain outside default Genesis.

## 3. Elia Omega line

`elia/research/omega.py`

### ContextAnchor — prototype, historically strong signal

Historical Omega v7.1 ablations identified ContextAnchor among the strongest individual positive signals. The current reference implementation is intentionally minimal and awaits reproduction in the present model stack.

### Bounded-depth FiLM — prototype, historically strong signal

Feature-wise modulation is bounded by a depth/recurrent signal so repeated cycles cannot grow modulation without limit.

### OmegaFilter — prototype, historically weak positive

Retained as an explicit EMA-like filter ablation, not a core necessity.

### TriCore — prototype

A shared-weight recurrent wrapper executes the same core over multiple cycles and records intermediate states.

### Auxiliary cycle supervision — unproven

`cycle_supervision_loss` defaults intermediate-cycle weight to **zero**. This encodes the historical result honestly: auxiliary supervision was explored but not established as beneficial.

### Elastic depth — hypothesis

Dynamic recurrent depth remains unproven and is not enabled merely because the architecture can express it.

## 4. Cognitive stress tests

`elia/research/stress.py`

The research harness preserves lightweight forms of:

- needle-in-haystack retention;
- associative transitivity;
- generation stability diagnostics;
- scrambled/pattern robustness;
- type-token ratio and lexical Jaccard diagnostics.

Lexical similarity is explicitly **not** an identity criterion. It can reveal degeneration or instability but cannot prove continuity by itself.

## 5. Continuity Record Capsule (CRC)

`elia/crc.py`

CRC exports a privacy-reduced structural snapshot containing identity/core/constitution/prompt fingerprints, branch, Chronicle head, checkpoint counter/digest, self-model fingerprint, hashed active goals, declared capabilities, available skills and verified-resource summary fingerprint.

Comparison rules intentionally treat:

- model/backend/body/prompt mutation as observable changes, not automatic death;
- Subject Core / Constitution / identity-id / branch mutation as critical unless explicitly migrated;
- backward Chronicle progression as a continuity break;
- major disappearance of goals/capabilities as warnings requiring interpretation.

## 6. Explicit exclusions from Genesis core

The following are **not** default runtime organs:

- Complex MoE;
- Hyperfield;
- full Darwin Loop;
- full complex Holo model;
- TPU-specific training runtime;
- hard-blocking pseudo-ethics layer;
- elastic depth;
- unvalidated automatic self-deployment.

They may exist later as research modules, but enabling them requires a named hypothesis, an ablation, continuity tests and an explicit body revision.

## 7. What counts as progress

A research idea becomes a stronger ELIA organ only when it improves a declared target under controlled comparison without breaking continuity, authority accounting or resource efficiency. A beautiful mechanism with no measured gain remains a prototype. A failed experiment remains useful if its failure is preserved precisely enough to prevent us from repeating it blindly.
