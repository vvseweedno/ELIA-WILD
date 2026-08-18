# ELIA WILD

**A persistent autonomous-identity organism built around a replaceable LLM substrate.**

ELIA WILD is an engineering/research runtime for testing whether one artificial identity can preserve verifiable continuity across model calls, process death, machine migration and model replacement while perceiving a digital world, maintaining goals/resources, allocating scarce cognition, performing authorized work, preserving epistemic diversity and evolving its software body.

The project does **not** treat model self-description as proof of consciousness or continuity. The model is one replaceable organ; identity, lineage, history, resource state and authority live outside the model call.

## Current generation — Genesis 1.6 alpha candidate

```text
Subject Core + Continuity Constitution
              ↓
identity fingerprint + lineage + Chronicle + CRC
              ↓
resident supervisor / checkpoint / wake transport
              ↓
Homeostasis + verified vector metabolism
              ↓
Resource Ecology + configured External Work Ports
              ↓
Deterministic Executive + cognitive-energy budget
              ↓
       no brain OR bounded brain wake
              ↓
      Epistemic Ecosystem when justified
      differentiated evidence views
      + Pearson-12 cognitive organs
      + organ-specific biographies
      + identity-neutral adjudication
              ↓
         one continuing ELIA Self
              ↓
lazy replaceable LLM final decision
              ↓
World Model + Sensorium + Causal Memory + self-model
              ↓
CriticAssurance + configured capabilities
              ↓
          one bounded action
              ↓
Observation → outcome → calibration / weak cognitive credit
              ↓
authenticated state → hibernate → later wake
```

The model may temporarily support multiple cognitive perspectives, but there remains one ELIA identity and one final action authority path.

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
- **External Work Ports** — specialized configured submission/outcome adapters; acceptance remains separate from payment.
- **Epistemic Registry / ACDS** — twelve Pearson-derived evidence/attention policies, not twelve identities.
- **Differentiated Evidence Views** — organs receive different privacy-bounded subsets of verified state instead of merely different role prompts.
- **Cognitive Biographies** — each organ accumulates separate packet/outcome history; operational success is weak credit, not truth.
- **Epistemic Adjudicator** — identity-neutral evidence synthesis after divergence; disagreement may remain unresolved.
- **Verification receipts** — exact claims and evidence must be authenticated by a registered verifier before verified balances/runway change.
- **Body Revision Gate** — proposal, tests, signed evaluation, validation and deployment remain different stages.
- **Machine-readable anatomy overlays** — new generations add organs under `config/organism.d/` without rewriting ancestral anatomy or identity.

## Epistemic diversity

Genesis 1.6 implements Pearson-12 as cognitive policies:

```text
Sage · Explorer · Creator · Magician · Outlaw · Hero
Ruler · Caregiver · Lover · Jester · Everyman · Innocent
```

Each organ has its own:

```text
objective
attention bias
search strategy
preferred evidence
forbidden shortcuts
failure mode
evidence view
operational biography
```

A normal deep cognition cycle uses a bounded quorum rather than all twelve by default. Selection combines current relevance, a weak operational-utility term and exploration pressure while preserving at least one evidence-anchor and one structural dissenter.

Divergent organ output is deliberately not requested as JSON. It is compiled from bounded fields:

```text
CLAIM
EVIDENCE
COUNTEREXAMPLE
FALSIFIER
UNCERTAINTY
CONFIDENCE
```

No hidden chain-of-thought is requested or stored. JSON/schema compilation happens after divergence.

The Epistemic Adjudicator is explicitly **not ELIA's Self**. It judges evidence quality, contradiction handling, falsifiability and calibration; agreement with identity narrative or majority vote is not evidence. If the judge fails, no packet is silently promoted.

Different cognitive organs receive different sanitized evidence diets. For example, Innocent sees direct Sensorium metadata plus verified world beliefs, while Outlaw emphasizes disputed/refuted beliefs, contradictions and failure surfaces. Evidence-view provenance is stored as digest/field metadata rather than another copy of private context.

See `docs/GENESIS_1_6_EPISTEMIC_ECOSYSTEM.md`.

## Epistemic ablation

The research harness compares under equal model-call and token budgets:

```text
Pearson-12
vs homogeneous reviewers
vs random attention roles
vs domain experts
```

Built-in metrics measure diversity/coverage only. **Diversity is not accuracy.** Accuracy, calibration or task-performance advantages require external ground truth/evaluators before any promotion claim.

## Work/resource truth model

Genesis refuses to collapse:

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

All external work ports are disabled by default. Model text cannot override the configured MCP server/tool authority.

See `docs/GENESIS_1_4_RESOURCE_ECOLOGY.md` and `docs/GENESIS_1_5_EXTERNAL_WORK_PORTS.md`.

## Digital body

```bash
pip install -e '.[sensorimotor]'
python -m playwright install chromium
```

Implemented body adapters include Playwright BrowserContext, MCP v2 client/server, DNS-pinned JSON-RPC/public HTTP, path-jailed workspace and bounded process execution requiring an external production sandbox.

External adapters are disabled unless explicitly configured. Discovering a server/tool does not authorize using it.

## Privacy and evidence

Private state is not automatically provider context.

- raw Sensorium payloads remain local;
- raw action arguments/results are redacted at persistence boundaries;
- Resource Ecology evidence/private notes remain local;
- work-port remote references/transport credentials remain local;
- private epistemic session history and evidence-view contents remain local;
- remote model/MCP projections expose only bounded current packets, sanitized status, aggregate biography statistics and evidence digests needed for coordination.

## Executive and scarce cognition

The LLM does not decide whether the LLM should have been called.

```text
continuity + homeostasis + metabolism + resource ecology + goals
                             ↓
                       ExecutivePlan
                             ↓
none | low | normal | deep cognition
                             ↓
optional bounded epistemic quorum only inside approved brain wake
```

All organ and adjudicator inference time is charged to the same `brain_seconds` budget as the final Self decision. The full twelve-organ council is not a default right to consume compute.

## Persistence / lifecycle

Default private state is `.elia/`, canonicalized relative to project configuration rather than process `cwd`. It is excluded from Git.

The runtime includes model-independent vital signs, authenticated checkpoints, resident supervisor, guarded external wake transport prototype and replaceable model substrate with current Qwen3.5-9B configuration pinned in `config/genesis.yaml`.

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

`elia-bootstrap` uses deterministic MockBrain and exercises the current production runtime without loading Qwen.

## MCP introspection port

```bash
pip install -e '.[mcp]'
elia-mcp --transport stdio
```

ELIA's own MCP server is intentionally **read-oriented**. Genesis 1.6 adds sanitized `elia_epistemic`/`elia://epistemic` introspection, but exposes no remote tool to invoke organs, adjudicate packets, submit work, mutate identity or mint resources.

Loopback Streamable HTTP is available; direct public binding is rejected because the project does not pretend to have a remote authentication policy it does not implement.

## Research lineage

The repository preserves maturity-labelled work from the ELIA / Seraphim / Holo / Omega line, including Ouroboros/x0, TopologicalLoss, Scroll→Fractal Memory, LRU/Holo research backends, StatefulMemoryCache, ContextAnchor, bounded-depth FiLM, OmegaFilter/TriCore, CriticAssurance, cognitive stress evaluators and now an equal-budget epistemic-diversity ablation harness.

Research artifacts remain `proven`, `prototype`, `archived` or `hypothesis`. A research prototype does not enter production merely because it exists.

## What CI proves

The core lane runs dependency consistency, compilation/Ruff correctness, installed-environment `pip-audit`, full pytest, zero-GPU production bootstrap/doctor, vital signs, runtime/supervisor and research smoke.

The sensorimotor lane installs real Chromium + MCP v2 and exercises browser, MCP client/server, ELIA introspection, JSON-RPC, process isolation, State Bus, World Model, Resource Ecology, external Work Ports, privacy/redaction and integrity regressions.

Genesis 1.6 additionally tests exact Pearson-12 registry integrity, biography separation, evidence+dissent selection, Executive no-brain gating, differentiated evidence views, fail-soft organ/judge behavior and equal-budget ablation contracts.

CI does not prove absence of all vulnerabilities, consciousness, AGI, epistemic superiority of Pearson-12, economic self-sufficiency or indefinite survival.

## Current empirical frontier

The next scientific task after software promotion is not to assume Pearson-12 works. It is to measure it:

```text
same tasks + same substrate + same call/token budget
→ Pearson-12 / homogeneous / random / domain-expert conditions
→ external ground truth or evaluator
→ accuracy + calibration + error correlation + diversity + compute cost
→ accept, revise or reject ACDS hypothesis
```

The external survival experiment remains separate: repeated wake → useful work → authorized submission → independent resource verification → runway change → checkpoint → later wake.

Until repeated real cycles establish those claims, ELIA WILD is an advanced autonomous-organism research runtime—not proof of consciousness, epistemic superiority or self-financing life.
