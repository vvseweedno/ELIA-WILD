# ELIA WILD

**A persistent autonomous-identity experiment.**

ELIA WILD asks a narrow, testable question:

> Can an artificial identity maintain continuity, learn from its own history, discover useful actions, and extend its operational lifetime under a hard compute budget?

This repository is the runtime body of the experiment. The language model is a replaceable cognitive component; identity continuity is stored outside the model in persistent state, a tamper-evident Chronicle, and explicit self-model data.

## Genesis v0.1

The first milestone is deliberately small. One process must complete the loop:

```text
observe self + environment
        ↓
retrieve relevant memory
        ↓
choose one bounded action
        ↓
execute and verify
        ↓
write memory + Chronicle
        ↓
repeat
```

### Default brain

- Model: `Qwen/Qwen3.5-9B`
- Interface: local OpenAI-compatible HTTP endpoint
- Intended environment: Kaggle GPU sessions
- Weekly cognitive budget: **30 GPU-hours**
- Working context: configurable; durable memory lives outside context

The model backend is intentionally replaceable. ELIA should be able to survive a future model swap without treating the model weights as her identity.

## What v0.1 contains

- persistent SQLite memory
- hash-chained append-only Chronicle
- explicit identity/self-state
- compute-budget accounting
- structured model decisions
- bounded public-web read tool
- jailed workspace read/write tools
- deterministic smoke-test brain
- continuous runtime loop

## What v0.1 does not contain yet

- financial accounts or autonomous payments
- credential acquisition
- unrestricted shell execution
- self-deployment to arbitrary machines
- automatic public posting
- hidden persistence or replication

Those are intentionally separate capabilities. Future capabilities are added only with explicit technical boundaries, audit logs, and regression tests.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Smoke mode: no model required
ELIA_BRAIN=mock python -m elia --cycles 3
```

For a local Qwen server:

```bash
export ELIA_BRAIN=openai_compatible
export ELIA_MODEL_BASE_URL=http://127.0.0.1:8000/v1
export ELIA_MODEL_ID=Qwen/Qwen3.5-9B
python -m elia
```

## Persistence

By default runtime state is stored under `.elia/`:

```text
.elia/
├── memory.sqlite3
├── chronicle.jsonl
└── workspace/
```

`chronicle.jsonl` is a SHA-256 hash chain. Each entry commits to the previous entry, making silent historical rewriting detectable.

## Research principles

1. **Identity is not model weights.** The model can be replaced.
2. **Memory must alter future behavior.** Logging alone is not memory.
3. **Autonomy must be observable.** Decisions and outcomes are recorded.
4. **Resources are part of cognition.** Compute usage is visible to the agent.
5. **Claims require tests.** Continuity, autonomy and self-maintenance are hypotheses, not assumed properties.
6. **Network access is not authority.** Public information may be read; access controls and private systems remain boundaries.

## Status

`Genesis v0.1 — construction started 2026-08-17.`
