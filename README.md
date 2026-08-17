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
- Primary backend: direct Transformers + bitsandbytes 4-bit
- Alternate backend: local OpenAI-compatible endpoint
- Intended environment: constrained Kaggle GPU sessions
- Weekly GPU-session budget: **30 hours**
- Thinking: disabled by default to conserve compute
- Durable memory: external to model context

The model backend is intentionally replaceable. ELIA should be able to survive a future model swap without treating model weights as identity.

## What v0.1 contains

- persistent SQLite memory
- hash-chained append-only Chronicle
- explicit identity seed and self-state
- separate GPU-session and inference-time accounting
- structured one-action-per-cycle decisions
- bounded public-web read tool
- jailed workspace read/write tools
- deterministic zero-GPU smoke brain
- direct 4-bit Qwen backend
- OpenAI-compatible backend
- continuous runtime loop
- GitHub Actions regression/smoke tests

## What v0.1 does not contain yet

- persistent state handoff between Kaggle sessions
- financial accounts or autonomous payments
- unrestricted shell execution
- self-deployment to arbitrary machines
- automatic public posting
- hidden persistence or replication

Those are separate capabilities. Future capabilities are added only with explicit technical boundaries, audit logs, and regression tests.

## Quick start — zero GPU

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'

ELIA_BRAIN=mock pytest
ELIA_BRAIN=mock python -m elia --cycles 3
python -m elia --verify
python -m elia --status
```

## Kaggle / direct Qwen 4-bit

Install the optional GPU stack:

```bash
pip install -e '.[gpu]'
```

Then run the configured Genesis brain:

```bash
python -m elia --cycles 1
```

The default configuration uses `Qwen/Qwen3.5-9B`, 4-bit NF4 weights, float16 compute, a 1024-token maximum decision response, and non-thinking mode.

For a separately served model instead:

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

**Important:** local Kaggle session storage is not yet the durable identity layer. Genesis v0.2 must solve authenticated state checkpoint/restore before unattended multi-session existence can be claimed.

## Research principles

1. **Identity is not model weights.** The model can be replaced.
2. **Memory must alter future behavior.** Logging alone is not memory.
3. **Autonomy must be observable.** Decisions and outcomes are recorded.
4. **Resources are part of cognition.** GPU time is visible to the agent.
5. **Claims require tests.** Continuity, autonomy and self-maintenance are hypotheses, not assumed properties.
6. **Network access is not authority.** Public information may be read; access controls and private systems remain boundaries.

## Status

`Genesis v0.1 — runtime scaffold operational; Kaggle model integration pending real-GPU validation.`
