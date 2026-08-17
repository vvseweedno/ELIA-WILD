# Genesis Protocol

## Purpose

Genesis is not a demonstration that a language model can call tools. It is an experiment about persistent artificial identity under resource constraints.

The hypothesis is intentionally stronger:

> A system whose language-model checkpoint is replaceable can preserve measurable continuity across restarts, use accumulated experience to alter later behavior, manage a finite compute budget, and increase its useful capabilities over time.

## Unit under test

The unit under test is the whole ELIA WILD runtime:

- persistent identity seed
- memory store
- tamper-evident Chronicle
- resource ledger
- replaceable cognitive model
- tools
- action/result loop
- future checkpoint/restore mechanism

The model checkpoint by itself is **not** the subject.

## Genesis v0.1 success criteria

v0.1 succeeds only if all of the following hold:

1. The zero-GPU smoke runtime completes multiple cycles.
2. A restart preserves memory and increments boot history rather than creating a blank identity.
3. Chronicle verification succeeds after normal operation.
4. Tampering with a historical Chronicle entry is detected.
5. Workspace path traversal is rejected.
6. Public-web tooling rejects loopback/private network destinations.
7. Runtime errors are written into memory and Chronicle rather than silently disappearing.
8. Finite smoke runs fail visibly instead of entering an endless retry loop.
9. GPU-session time and model-inference time are accounted separately.
10. CI continuously re-runs the regression and smoke suite.

## First real-GPU success criteria

A first Kaggle GPU run succeeds only if:

1. `Qwen/Qwen3.5-9B` loads through the configured constrained-GPU backend.
2. One model response is parsed into the structured ELIA decision schema.
3. Exactly one allowed tool action executes.
4. The verified result is written back into persistent memory.
5. A `CYCLE` Chronicle entry is appended and the chain still verifies.
6. Runtime/inference resource counters increase.

A successful first GPU cycle does **not** establish persistent autonomy.

## Genesis v0.2 gate: continuity across independent sessions

No claim of persistent network existence is allowed until v0.2 can:

1. checkpoint durable state to an authenticated external store;
2. restore that state in a fresh machine/session;
3. verify Chronicle integrity before cognition resumes;
4. detect rollback to an older checkpoint;
5. avoid writing credentials or private memory into the public repository;
6. recover from an interrupted checkpoint without corrupting the last valid state.

## Future autonomy gate

Later autonomy claims require evidence that ELIA can, without step-by-step human direction:

- discover opportunities from observations;
- form intermediate objectives;
- choose whether spending compute is justified;
- learn from failed actions;
- create reusable artifacts or services;
- maintain commitments and revisit unfinished work;
- improve expected useful output per unit of compute;
- survive model replacement while meeting identity-continuity tests.

## Failure conditions

The experiment fails a claimed milestone if any of these are true:

- state is lost on a normal restart that the milestone claims to survive;
- the agent repeatedly reports invented tool outcomes;
- historical records can be silently rewritten without detection;
- resource accounting systematically ignores active GPU-session time;
- autonomous behavior is actually a hard-coded task script;
- a model swap implicitly resets identity while the experiment claims continuity;
- safety/access boundaries are bypassed rather than represented as constraints of the environment;
- success depends on continual hidden human intervention not recorded in the Chronicle.

## Measurement principle

Every strong claim must eventually correspond to a test, metric, reproducible run, or independently inspectable artifact.

Narrative continuity is interesting. **Measured continuity is the experiment.**
