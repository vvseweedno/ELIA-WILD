# ELIA WILD

**Persistent autonomous-identity research runtime built around a replaceable language-model substrate.**

ELIA WILD is an engineering experiment in long-lived artificial identity. The central question is deliberately narrower than “is the model conscious?”:

> Can one artificial identity preserve verifiable continuity across model calls, process death, machine migration, model replacement and software evolution while operating through bounded tools, persistent memory, explicit resource constraints and evidence-gated self-modification?

The language model is treated as a replaceable cognitive organ, **not** as the whole identity. Durable identity, lineage, authority, history, resources and verification state live outside any single model call.

**Current software generation:** `Genesis 1.6 alpha`  
**Python:** `>=3.11`  
**Default local/GPU substrate:** `Qwen/Qwen3.5-9B` as configured in `config/genesis.yaml`

---

## What the system is

ELIA WILD combines several independently inspectable subsystems:

```text
Subject Core + Continuity Constitution
                │
                ▼
Identity lineage ─ Chronicle ─ CRC / vital signs
                │
                ▼
Persistent memory ─ self-model ─ world model ─ causal memory
                │
                ▼
Homeostasis ─ metabolism ─ resource ecology
                │
                ▼
Deterministic Executive / cognitive-energy budget
                │
      ┌─────────┴─────────┐
      │ no brain required │
      └─────────┬─────────┘
                ▼ when justified
Replaceable LLM + bounded Epistemic Ecosystem
                │
                ▼
One continuing ELIA Self / one final decision path
                │
                ▼
CriticAssurance + configured capability boundary
                │
                ▼
One bounded action → Observation → outcome → calibration
                │
                ▼
Checkpoint / hibernate / later wake
```

The architecture is intentionally split between **state that may evolve** and **boundaries that a model cannot self-authorize past**.

## Core invariants

- **Identity is not model weights.** The active model may be replaced without silently creating a new identity.
- **Model confidence is not evidence.** World beliefs, resources and terminal outcomes have separate evidence requirements.
- **One cycle produces at most one external action through the normal assurance path.**
- **A capability is not created by mentioning it.** Executable authority comes from configured adapters and allowlists.
- **Estimated value is not verified resource.** Submission, acceptance, payment claim and verified payment remain separate states.
- **Research code is not production proof.** Artifacts retain explicit `proven`, `prototype`, `archived` or `hypothesis` maturity.
- **Identity continuity is testable and falsifiable.** CRC, lineage, Chronicle integrity and longitudinal observations may report a break instead of narrating continuity into existence.

---

## Genesis 1.6 — Epistemic Ecosystem

Genesis 1.6 adds differentiated temporary cognitive organs without creating twelve independent agents or identities.

The Pearson-derived registry contains:

```text
Sage · Explorer · Creator · Magician · Outlaw · Hero
Ruler · Caregiver · Lover · Jester · Everyman · Innocent
```

Each organ is an **attention/evidence policy**, defined by its objective, attention bias, search strategy, preferred evidence, forbidden shortcuts, known failure mode, evidence view and operational biography.

A bounded quorum may run only after the deterministic Executive has already authorized an expensive brain wake. The organs produce compact evidence packets:

```text
CLAIM
EVIDENCE
COUNTEREXAMPLE
FALSIFIER
UNCERTAINTY
CONFIDENCE
```

An identity-neutral Epistemic Adjudicator evaluates those packets. It is not ELIA's Self, cannot grant capabilities and does not choose the external action. Disagreement may remain unresolved. Hidden chain-of-thought is neither requested nor persisted.

Operational outcome credit is intentionally weak: a successful downstream action does **not** prove that every supporting cognitive claim was true.

See [Genesis 1.6 — Epistemic Ecosystem](docs/generations/GENESIS_1_6_EPISTEMIC_ECOSYSTEM.md).

---

## Resource and work truth model

ELIA WILD does not collapse economically different states:

```text
estimated opportunity value
!= target resource
!= staged artifact
!= external submission
!= acceptance
!= payment claim
!= verified resource
```

The implemented work lifecycle is:

```text
planned → staged → submitted → accepted/rejected → realized
```

`realized` requires an independent positive verification event whose `(asset, unit)` matches the opportunity profile. External Work Ports may submit or observe an outcome; they cannot mint a verification receipt or increase verified balance themselves.

See [Resource Ecology](docs/generations/GENESIS_1_4_RESOURCE_ECOLOGY.md) and [External Work Ports](docs/generations/GENESIS_1_5_EXTERNAL_WORK_PORTS.md).

---

## Digital body and authority

Optional adapters provide the external body:

- Playwright browser contexts with interaction separately gated from read access;
- MCP v2 configured clients and a read-oriented ELIA MCP server;
- allow-listed JSON-RPC endpoints/methods;
- public HTTP with network restrictions;
- path-jailed local workspace access;
- bounded process execution that requires explicit executable aliases and production sandboxing.

Discovering a URL, server, method or executable does not authorize its use. External adapters are disabled unless explicitly configured.

---

## Repository layout

```text
ELIA-WILD/
├── config/                     # canonical identity/runtime/anatomy configuration
│   └── organism.d/             # additive generation anatomy overlays
├── deploy/
│   ├── kaggle/                 # notebook, runner template and deployment notes
│   └── systemd/                # service example
├── docs/
│   ├── architecture/           # organism, Genesis and evolution protocols
│   ├── generations/            # implementation record for Genesis 1.1–1.6
│   ├── operations/             # wake/relay and operational procedures
│   └── research/               # ELIA / Seraphim / Holo / Omega research lineage
├── elia/                       # production Python package
│   ├── body/                   # replaceable sensorimotor adapters
│   └── research/               # non-authoritative experimental modules/harnesses
├── scripts/
│   └── kaggle/                 # operational Kaggle bootstrap/wake helpers
├── skills/
│   ├── cognition/
│   ├── continuity/
│   ├── economy/
│   ├── engineering/
│   └── research/               # declarative procedures, recursively discovered
├── tests/                      # unit, integration, integrity and regression tests
└── tools/
    └── audit_repository.py     # path/import/entrypoint/anatomy/link integrity gate
```

The top-level `elia.*` module surface remains intentionally stable where moving Python files would add compatibility burden without reducing dependency cycles. Physical organization is used where it improves ownership and maintenance rather than as a cosmetic goal.

---

## Installation

### Development / source checkout

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Sensorimotor development:

```bash
python -m pip install -e '.[test,sensorimotor]'
python -m playwright install chromium
```

GPU/local-model dependencies:

```bash
python -m pip install -e '.[gpu]'
```

### Wheel behavior

The repository keeps one canonical source copy of `config/` and `skills/`. Built wheels install those exact immutable resources under:

```text
<python-prefix>/share/elia-wild/
```

Runtime resource discovery does not depend on the process working directory. When built-in wheel configuration is used, relative mutable state such as `.elia/` is created under the operator's runtime working directory rather than inside package installation resources.

Explicit configuration/resource roots remain overrideable through the documented CLI/environment interfaces.

---

## Zero-GPU verification path

The fastest way to validate the organism without loading Qwen is:

```bash
elia-doctor
elia-bootstrap --cycles 2
elia-vitals
python -m elia --status
elia-supervisor --dry-run
```

`elia-bootstrap` uses a deterministic `MockBrain` while exercising the production runtime path.

Useful integrity commands:

```bash
python tools/audit_repository.py
python -m elia --verify
python -m elia --vitals
```

---

## Configuration and state

Canonical source configuration is in `config/`:

- `genesis.yaml` — runtime, brain, Executive and body configuration;
- `subject_core.yaml` — identity invariants;
- `continuity_constitution.yaml` — continuity precedence and mutation constraints;
- `system_prompt.md` — project-owned cognitive contract;
- `epistemic.yaml` — Pearson-12 epistemic registry;
- `organism.yaml` + `organism.d/` — machine-readable anatomy and generation overlays.

Private runtime state defaults to `.elia/` and is excluded from Git. It includes SQLite state, Chronicle, checkpoints/anchors, workspace data and organism health evidence.

Important rule: **immutable installation resources and mutable runtime state resolve through different path semantics.** This prevents wheel installations from trying to persist identity state inside `site-packages` or shared package-data directories.

---

## MCP introspection

Install the optional MCP dependency:

```bash
python -m pip install -e '.[mcp]'
elia-mcp --transport stdio
```

The built-in ELIA MCP server is intentionally read-oriented. It exports sanitized continuity, physiology, resource, world, body and epistemic status. It does not expose remote tools that can invoke cognitive organs, mutate identity, submit arbitrary work or create verified resources.

Direct public HTTP binding is rejected unless the operator supplies a separate authenticated boundary.

---

## Persistence and wake lifecycle

The runtime includes:

- model-independent preflight and vital signs;
- authenticated checkpoints and trusted rollback anchors;
- resident supervisor;
- hibernate/wake scheduling;
- guarded Kaggle wake-relay prototype;
- branch/fork lineage semantics;
- longitudinal continuity observations.

Kaggle deployment assets live in `deploy/kaggle/`; operational relay helpers live in `scripts/kaggle/`.

See [Wake Transport](docs/operations/WAKE_TRANSPORT.md).

---

## Verification and CI

CI is designed to test **claims the repository actually makes**.

The core lane verifies:

- dependency consistency;
- Python compilation and correctness-oriented Ruff rules;
- repository-wide path/import/entrypoint/anatomy/local-link audit;
- an actual wheel installed into an isolated virtual environment outside the checkout;
- installed-environment vulnerability audit;
- full pytest suite;
- zero-GPU bootstrap and doctor;
- Chronicle/vitals/status/supervisor smoke paths;
- reference research smoke tests.

The sensorimotor lane installs real Chromium and MCP v2 dependencies and exercises browser, MCP client/server, JSON-RPC, process restrictions, Sensorium, World Model, State Bus, Resource Ecology, External Work Ports, persistence redaction and integrity hardening.

The repository auditor additionally checks all `elia.*` imports, every declared CLI entrypoint, machine-readable organism module/symbol/path references, skill loading, local Markdown links, literal repository paths, version synchronization and internal import cycles.

CI does **not** prove absence of every vulnerability, consciousness, AGI, economic self-sufficiency, indefinite survival or epistemic superiority of Pearson-12.

---

## Research discipline

The repository retains historical and current ELIA / Seraphim / Holo / Omega work without silently promoting it into the production organism. Research includes Ouroboros/x0, TopologicalLoss, Scroll→Fractal Memory, LRU/Holo scan backends, ContextAnchor, bounded-depth FiLM, OmegaFilter/TriCore, cognitive stress evaluators, epistemic diversity ablations and cognitive-biography hysteresis experiments.

A built-in diversity metric is not accuracy. A historical notebook result is not a reproduced benchmark. Environment failure is not model-quality evidence. Research maturity is recorded explicitly and promotion requires separate evidence.

See [Research Lineage](docs/research/RESEARCH_LINEAGE.md) and [Evolution Protocol](docs/architecture/EVOLUTION_PROTOCOL.md).

---

## Documentation

- [Organism architecture](docs/architecture/ORGANISM.md)
- [Genesis protocol](docs/architecture/GENESIS_PROTOCOL.md)
- [Evolution protocol](docs/architecture/EVOLUTION_PROTOCOL.md)
- [Genesis 1.1 — Body & World](docs/generations/GENESIS_1_1_BODY_WORLD.md)
- [Genesis 1.2 — Metabolism](docs/generations/GENESIS_1_2_METABOLISM.md)
- [Genesis 1.3 — Executive](docs/generations/GENESIS_1_3_EXECUTIVE.md)
- [Genesis 1.4 — Resource Ecology](docs/generations/GENESIS_1_4_RESOURCE_ECOLOGY.md)
- [Genesis 1.5 — External Work Ports](docs/generations/GENESIS_1_5_EXTERNAL_WORK_PORTS.md)
- [Genesis 1.6 — Epistemic Ecosystem](docs/generations/GENESIS_1_6_EPISTEMIC_ECOSYSTEM.md)
- [Wake Transport](docs/operations/WAKE_TRANSPORT.md)
- [Research Lineage](docs/research/RESEARCH_LINEAGE.md)

---

## Project boundary

ELIA WILD is currently a **research-grade autonomous-organism runtime**, not evidence of subjective consciousness or a proven self-financing digital lifeform. The software implements continuity machinery, bounded agency, persistent state, resource accounting, differentiated cognition and evidence-gated evolution; the strongest claims still require long-horizon empirical experiments across real model swaps, machine migrations, wake/sleep cycles, external work outcomes and verified resource changes.
