# Genesis 1.6 — Epistemic Ecosystem

Genesis 1.6 introduces **differentiated cognition without fragmented identity**.

There is still one ELIA identity, one lineage, one Executive, one capability boundary and one final action decision. Pearson-derived cognitive organs are temporary evidence-seeking processes inside an already-authorized cognition envelope.

## Why this exists

Multiple copies of one model with nearly identical prompts can produce correlated errors and superficial consensus. Genesis 1.6 tests a stronger hypothesis:

> durable cognitive differentiation emerges from different attention/evidence policies, different evidence views, and different operational biographies—not merely from role labels.

The hypothesis is testable and may be rejected by ablation.

## Architecture

```text
verified organism context
        ↓
Deterministic Executive
        ├── no brain → no epistemic council
        └── brain wake
                ↓
      trigger? deep tier / world contradiction
                ↓
      bounded organ selection
      evidence anchor + dissent guaranteed
                ↓
    differentiated evidence views
                ↓
 temporary cognitive organs
  claim / evidence / counterexample /
  falsifier / uncertainty / confidence
                ↓
 identity-neutral Epistemic Adjudicator
                ↓
 synthesis OR preserved disagreement
                ↓
        one continuing ELIA Self
                ↓
      ordinary CriticAssurance
                ↓
          one bounded action
                ↓
 observed outcome → weak operational credit
```

## Pearson-12 registry

`config/epistemic.yaml` defines exactly:

```text
Sage
Explorer
Creator
Magician
Outlaw
Hero
Ruler
Caregiver
Lover
Jester
Everyman
Innocent
```

Each entry defines:

```text
objective
attention_bias
search_strategy
preferred_evidence
forbidden_shortcuts
failure_mode
tags
role_classes
```

The names are labels for evidence/attention policies. They are not independent identities or permissions.

## Evidence diets

`EvidenceViewProjector` derives every organ view from the provider-safe context boundary. Different organs receive deliberately different state subsets.

Examples:

- Sage: world beliefs, Sensorium metadata, causal statistics, metacognitive calibration;
- Hero: goals, capabilities, skills, work/resource execution state;
- Ruler: metabolism, homeostasis, body, lineage and Executive energy;
- Outlaw: disputes, contradictions, drift, incomplete transitions and failure surfaces;
- Innocent: direct Sensorium metadata plus only verified world beliefs, minimizing inherited hypotheses.

The raw view is not duplicated into long-term storage. `EpistemicViewStore` persists only its digest, included fields, size and failure state.

Absence from an organ's partial evidence view is explicitly **not evidence of absence from the world**.

## Cognitive biographies

`CognitiveBiographyStore` preserves separate histories for each organ. A biography includes packet history, support-selection frequency, confidence and downstream operational outcome association.

Outcome association is deliberately weak credit assignment:

```text
action succeeded after organ recommendation
!=
organ claim was true
```

This prevents the system from teaching itself epistemic certainty from mere temporal correlation.

## Divergence before structure

Organ output is not requested as JSON. It uses a bounded tagged text protocol:

```text
CLAIM
EVIDENCE
COUNTEREXAMPLE
FALSIFIER
UNCERTAINTY
CONFIDENCE
```

No hidden chain-of-thought is requested or persisted. Structured JSON is introduced only at the later adjudication/decision compilation boundary.

## Identity-neutral adjudication

The Epistemic Adjudicator is explicitly not the Self and receives no special authority from ELIA identity narrative.

It evaluates:

- evidence quality;
- contradiction handling;
- falsifiability;
- uncertainty calibration;
- relevance to the verified question.

It may preserve disagreement. Majority vote is not evidence.

If adjudication fails, no packet is automatically promoted.

## Failure behavior

The council is fail-soft:

- one failed organ is recorded and the remaining quorum may continue;
- below minimum quorum, synthesis fails closed;
- adjudicator failure preserves packets but selects none;
- no council failure grants capabilities or increases cognition authority;
- unresolved sessions remain evidence rather than being rewritten as success/failure narratives.

## Cognitive energy

ACDS cannot wake the model independently. It runs only inside `ExecutiveOrganismRuntime._before_brain()` after `wake_brain=true`.

All organ and adjudicator inference time is charged to the same weekly `brain_seconds` ledger as the final Self decision. Default runtime policy invokes a bounded quorum and does not run all twelve organs on every cycle.

## Privacy

Remote providers receive only bounded epistemic projections:

- current organ IDs;
- current evidence packets;
- current neutral adjudication;
- aggregate biography statistics.

They do not automatically receive private session transcripts, stored evidence-view contents or full cognitive biography history.

The read-oriented ELIA MCP port exposes the same sanitized status and cannot invoke cognitive organs or adjudication.

## Ablation requirement

`EpistemicAblationHarness` compares under the same number of model calls and the same token ceiling:

```text
Pearson-12
homogeneous reviewers
random attention roles
domain experts
```

Built-in metrics include response diversity, unique-claim ratio, counterexample/falsifier coverage and confidence spread.

Those metrics are **not accuracy**. Factual accuracy, calibration improvement or task-performance claims require external ground truth/evaluators.

## Promotion gates

Genesis 1.6 is eligible for promotion only when:

```text
exact Pearson-12 registry validates
evidence/dissent quorum invariant passes
Executive hibernation invokes zero organs
per-organ evidence views are structurally different
private raw context is absent from evidence-view audit storage
single-organ failure degrades without crashing
below-quorum and judge failures promote nothing
ablation conditions receive equal compute budgets
provider/MCP projections omit private biography transcripts
production CLI/bootstrap/MCP all use 1.6 state
full pytest + pip-audit pass
real Chromium/MCP regression lane passes
vital signs/CRC stay healthy across the 1.5 → 1.6 body mutation
```

Genesis 1.5 remains the direct rollback ancestor until those gates pass.
