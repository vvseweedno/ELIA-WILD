# ELIA WILD

**A persistent autonomous-identity organism built around a replaceable LLM substrate.**

ELIA WILD is an engineering/research runtime for testing whether one artificial identity can preserve verifiable continuity across model calls, process death, machine migration and model replacement while perceiving a digital world, maintaining goals/resources, allocating scarce cognition, performing authorized work and evolving its software body.

The project does **not** treat model self-description as proof of consciousness or continuity. The model is one replaceable organ; identity, lineage, history, resource state and authority live outside the model call.

## Current generation — Genesis 1.5 alpha

```text
Subject Core + Continuity Constitution
              ↓
identity fingerprint + lineage + Chronicle + CRC
              ↓
resident supervisor / checkpoint / wake transport
              ↓
Homeostasis + verified vector metabolism
              ↓
Resource Ecology
bottleneck → exact (asset, unit) opportunity → work plan
              ↓
Deterministic Executive + cognitive-energy budget
              ↓
lazy replaceable LLM brain
              ↓
World Model + Sensorium + Causal Memory + self-model
              ↓
CriticAssurance + configured capabilities
              ↓
local work: planned → staged
              ↓
configured External Work Port
              ↓
real MCP submission Observation
              ↓
submitted → pending → accepted/rejected
              ↓
SEPARATE verifier boundary
              ↓
verified resource event → realized → new runway
```

The final verifier arrow is intentionally **not** owned by the submission port. Acceptance is not payment.

## Structural organs

- **Subject Core / Continuity Constitution** — immutable identity/continuity invariants.
- **Identity lineage + Chronicle + CRC** — tamper-evident history, branch/fork semantics and continuity measurement.
- **Persistent memory** — goals, experience, capability health, adaptive self-model and metacognitive state.
- **Sensorium / World Model / Causal Memory** — observed tool outcomes, revisable beliefs and intervention history without confusing correlation for causation.
- **Digital body** — browser, MCP v2, JSON-RPC, bounded processes, public HTTP and private workspace behind explicit capability contracts.
- **Homeostasis / Metabolism** — deterministic pressures, independent `(asset, unit)` balances, obligations, burn and runway.
- **Executive Cortex** — decides whether expensive cognition should wake, what verified pressure/goal receives focus and which token/thinking envelope may be used.
- **Cognitive Energy** — measured inference cost may constrain later cognition; low cost never auto-grants deeper reasoning.
- **Resource Ecology** — separates abstract opportunity value, exact target resource and cryptographically verified resource.
- **External Work Ports** — specialized configured submission/outcome adapters. The model chooses a declared port/work item, never arbitrary transport/server/tool authority.
- **Verification receipts** — exact claims and evidence must be authenticated by a registered verifier before verified balances/runway change.
- **Body Revision Gate** — proposal, tests, signed evaluation, validation and deployment remain different stages.
- **Machine-readable anatomy overlays** — new generations add organs under `config/organism.d/` without rewriting ancestral anatomy or identity.

## Work/resource truth model

Genesis deliberately refuses to collapse these states:

```text
estimated opportunity value
!= target resource
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

`realized` requires accepted work plus a positive, cryptographically verified resource event whose exact `(asset, unit)` matches the opportunity profile.

Genesis 1.5 adds the first real external-work bridge:

- `submit_work` — staged UTF-8 artifact → fixed configured MCP port → machine-readable `submission_ref` → durable Observation → `submitted`;
- `check_work_outcome` — fixed configured MCP outcome tool → `pending|accepted|rejected` → durable Observation → work lifecycle update;
- neither capability can issue a verification receipt or increase a verified balance.

Port configuration is authority. Model text cannot override the configured MCP server/tool by inventing action arguments.

All ports are disabled by default:

```yaml
tools:
  work_ports:
    enabled: false
    ports: {}
```

See `docs/GENESIS_1_4_RESOURCE_ECOLOGY.md` and `docs/GENESIS_1_5_EXTERNAL_WORK_PORTS.md`.

## Digital body

Install the complete sensorimotor test/runtime dependencies:

```bash
pip install -e '.[sensorimotor]'
python -m playwright install chromium
```

Implemented body adapters include:

- Playwright BrowserContext navigation/snapshot/screenshot and separately gated interaction;
- MCP v2 configured client discovery/resource/tool calls;
- ELIA's own read-oriented MCP v2 status port;
- DNS-pinned JSON-RPC and public HTTP transport;
- path-jailed private workspace;
- bounded process execution requiring an external sandbox for production.

External adapters are disabled unless explicitly configured. Discovering a server/tool does not authorize using it.

## Privacy and evidence

Private state is not automatically provider context.

- raw Sensorium payloads remain local;
- raw action arguments/results are redacted at persistence boundaries;
- Resource Ecology raw evidence/private notes remain local;
- work-port `submission_ref`, remote response bodies and transport credentials remain local;
- model/MCP status projections contain only bounded IDs, state, scores, public source metadata and evidence digests needed for coordination.

MCP tool results are treated as machine evidence only when they are actual JSON objects from MCP structured content or a JSON-object text block. Prose, arrays, malformed JSON and scalar JSON never become structured success evidence.

## Executive and scarce cognition

The LLM does not decide whether the LLM should have been called.

```text
continuity + homeostasis + metabolism + resource ecology + goals
                             ↓
                       ExecutivePlan
                             ↓
none | low | normal | deep cognition
```

Default envelopes are configured in `config/genesis.yaml`. A `halt`/`hibernate` Executive plan can avoid loading the expensive model entirely. Measured brain-seconds are fed back into later planning.

## Persistence / lifecycle

Default private state is `.elia/`, canonicalized relative to project configuration rather than process `cwd`. It is excluded from Git.

The runtime includes:

- model-independent vital signs;
- authenticated checkpoints with rollback anchor and fail-closed capture consistency;
- resident supervisor;
- guarded external wake transport prototype for ephemeral Kaggle GPU sessions;
- replaceable model substrate with current Qwen3.5-9B configuration pinned in `config/genesis.yaml`.

Zero-GPU proof path:

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

`elia-bootstrap` always uses the deterministic MockBrain; it proves the current organism path without loading the configured GPU model.

## MCP introspection port

```bash
pip install -e '.[mcp]'
elia-mcp --transport stdio
```

ELIA's own MCP server is intentionally **read-oriented**. It exposes sanitized identity/status/Executive/Resource-Ecology/Work-Port/world/sensorium/body state. It does not publicly expose `submit_work`, `check_work_outcome`, payment or body mutation tools.

Loopback Streamable HTTP is available, but direct public binding is rejected because the project does not pretend to implement a remote authentication policy it does not have.

## Research lineage

The repository preserves executable/maturity-labelled work from the ELIA / Seraphim / Holo / Omega line, including Ouroboros/x0, TopologicalLoss, Scroll→Fractal Memory, LRU/Holo scan research backends, StatefulMemoryCache, ContextAnchor, bounded-depth FiLM, OmegaFilter/TriCore, CriticAssurance and cognitive stress evaluators.

Research artifacts remain `proven`, `prototype`, `archived` or `hypothesis`. A research prototype does not enter production merely because it exists.

See `docs/RESEARCH_LINEAGE.md` and `docs/EVOLUTION_PROTOCOL.md`.

## What CI proves

The core lane runs dependency consistency, compilation/Ruff correctness, installed-environment `pip-audit`, full pytest, zero-GPU production bootstrap/doctor, vital signs, runtime/supervisor and research smoke.

The sensorimotor lane installs real Chromium + MCP v2 and exercises browser, MCP client/server, ELIA MCP introspection, JSON-RPC, process isolation, State Bus, World Model, Resource Ecology, **real in-process external Work Port submission/outcome**, privacy/redaction and integrity regression tests.

CI does not prove absence of all vulnerabilities, consciousness, AGI, economic self-sufficiency or indefinite survival.

## Current empirical frontier

Software generations 1.0–1.5 progressively close the internal loop. The decisive next external experiment is:

```text
wake
→ restore
→ verified resource deficit
→ Executive arbitration
→ discover/qualify real lawful opportunity
→ produce staged work
→ configured authorized submission
→ observe acceptance/rejection
→ independent verifier reads real resource evidence
→ signed VerificationReceipt
→ verified balance/runway change
→ checkpoint / hibernate / later wake
```

Until repeated real cycles complete this chain without a human manually asserting every transition, ELIA WILD is an advanced autonomous-organism research runtime — not a proven self-financing organism.
