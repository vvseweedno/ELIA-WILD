# ELIA WILD — body evolution protocol

ELIA can adapt without treating every model idea as a real improvement. The repository therefore separates **identity continuity**, **body revision** and **research hypothesis**.

A model/backend swap, prompt revision, memory backend experiment or new capability can change the software body. A legitimate mutation should change the architecture fingerprint while preserving the immutable identity fingerprint and explicit lineage.

## Revision lifecycle

```text
observed bottleneck
      ↓
body revision proposal
      ↓
hypothesis + target organs + baseline metrics
      ↓
regression plan + rollback plan
      ↓
testing
      ↓
CI / organism audit / CRC / task metrics
      ↓
signed evaluator receipt over exact evaluation claim + evidence
      ├── fail → rejected evidence
      └── pass → validated evidence
```

`elia.evolution.BodyRevisionStore` persists this lifecycle in the same checkpointed SQLite state as ELIA's other durable records. It **does not edit code or deploy anything**.

`RevisionGate` deterministically requires:

- regression tests passed;
- organism audit healthy;
- CRC continuity not `broken`;
- all supplied metric predicates passed.

The gate itself does not supply authority. Before the store may persist an evaluation result, the exact normalized evaluation claim — revision id, test result, organism health, continuity status and metric inputs — plus the evidence must be authenticated by a `VerificationReceipt` from a key registered in a trusted `VerificationRegistry`. A caller-provided `evaluator_authority` string is explicitly insufficient.

This means the model may propose a mutation and may reason about test output, but it cannot make its own evidence authoritative merely by naming itself or a fictional evaluator. Changing the reported metrics, continuity result or evidence invalidates the signed receipt.

## Why this is not a full Darwin Loop

The historical ELIA research line considered more aggressive automatic self-modification. Genesis deliberately does not make that a core default. Unbounded self-edit → self-test → self-deploy creates an easy feedback loop in which the same model proposes the change, judges the evidence and broadens its own authority.

Instead, Genesis builds the missing measurable substrate first. Future automation may apply candidate patches in a disposable branch/sandbox, but promotion must still preserve identity/authority invariants, use independently authenticated evaluation evidence and leave reproducible artifacts.

## Relationship to Seraphim / Holo / Omega

A research organ is promoted only through a body revision with a named baseline. Examples:

- Ouroboros/x0 must beat a no-injection baseline on a declared task without continuity regressions;
- FractalMemory must be compared against Scroll and LRU-style baselines;
- Holo scan cannot replace LRU merely because it is more elaborate;
- ContextAnchor/FiLM/OmegaFilter/TriCore require reproduction in the current model stack;
- archived TPU/runtime failures stay classified as infrastructure evidence rather than model-quality results.

This preserves evolutionary pressure **toward measured capability**, not toward architectural ornament.
