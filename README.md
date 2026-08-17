# ELIA WILD

**A persistent autonomous-identity experiment under a hard compute budget.**

ELIA WILD asks a narrow, falsifiable question:

> Can an artificial identity preserve continuity across model calls and machines, derive needs from its own verified state, maintain durable goals, learn from outcomes, and extend its operational lifetime without a human supplying every next task?

This repository is the runtime body of the experiment. The language model is a replaceable cognitive component. Durable identity state lives outside model weights.

## Current architecture — Genesis v0.3

```text
verified persistent state
        ↓
observe self + environment
        ↓
derive explicit needs
        ↓
recall memory + durable goals
        ↓
model chooses one bounded action
+ proposes bounded goal updates
        ↓
execute + capture outcome
        ↓
write memory + goal events + Chronicle
        ↓
persist next intended wake time
        ↓
authenticated checkpoint / machine handoff
        ↓
repeat
```

The design deliberately separates three things:

- **brain** — replaceable inference model;
- **identity state** — memory, goals, Chronicle, scheduler and resource ledger;
- **authority** — the bounded tool layer that determines which external actions actually exist.

A smarter model does not automatically receive broader authority.

## Default brain

- Model: `Qwen/Qwen3.5-9B`
- Primary backend: direct Transformers + bitsandbytes 4-bit
- Alternate backend: local OpenAI-compatible endpoint
- Intended environment: constrained Kaggle GPU sessions
- Weekly GPU-session budget: **30 hours**
- Thinking: disabled by default to conserve compute
- Durable memory: external to model context

The model backend is intentionally replaceable. A model swap must not silently become an identity reset.

## Implemented continuity substrate

### Persistent memory

SQLite stores episodic records, runtime metadata and resource metrics. Memory is recalled into later cognitive cycles rather than functioning only as an audit log.

### Durable goals

Goals are first-class persistent objects with:

- title and description;
- priority;
- status (`active`, `blocked`, `completed`, `abandoned`);
- optional parent goal;
- event history and evidence.

The brain may propose a small number of goal mutations per cycle. The runtime bounds the active goal set, suppresses duplicate active goals, and rejects completion/abandonment claims without evidence.

### State-derived needs

`elia/autonomy.py` derives explicit maintenance pressures from verified runtime state rather than allowing the model to invent arbitrary hidden drives. Current signals include:

- Chronicle integrity failure;
- absence of an authenticated checkpoint;
- low/exhausted GPU runway;
- recent runtime errors;
- absence of active goals;
- all goals being blocked.

Needs are observable in `python -m elia --status` and in the model context.

### Persistent scheduler intent

After every completed cycle ELIA records the next time it intended to wake. A later process can see the prior intent and how late the new runtime is relative to it. The wake intent is part of the SQLite state and therefore travels through authenticated checkpoints.

### Tamper-evident Chronicle

`chronicle.jsonl` is an append-only SHA-256 hash chain. Every entry commits to the previous entry. Silent modification of recorded history is detectable at boot and with `--verify`.

### Authenticated machine migration

Genesis v0.2 added versioned `.eliacp` state checkpoints containing:

- a consistent SQLite snapshot;
- Chronicle state and verified head;
- private workspace files;
- resource ledger, goals, scheduler and identity metadata carried by SQLite;
- per-file SHA-256 hashes and sizes;
- an HMAC-authenticated manifest.

The HMAC key is external to both the public repository and checkpoint archive. Restore validates authentication, file integrity, SQLite integrity, Chronicle integrity/head and identity name **before the model is started**.

Existing installations maintain a local checkpoint counter/digest anchor. A completely fresh machine can require `--expected-checkpoint-digest` so a valid but older checkpoint cannot silently masquerade as the newest known state.

## Current tool authority

The present Genesis body intentionally exposes only bounded primitives:

- `noop`
- private workspace listing
- UTF-8 workspace read/write inside a path jail
- public HTTP/HTTPS GET with private/loopback/link-local/reserved destinations rejected

There is currently **no unrestricted shell, credential acquisition, autonomous payment, arbitrary remote deployment, hidden persistence or uncontrolled replication**. Those are authority changes, not intelligence upgrades, and are treated as separate milestones.

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

Run exactly one real cognitive cycle first:

```bash
python -m elia --cycles 1
python -m elia --verify
python -m elia --status
```

The default configuration uses `Qwen/Qwen3.5-9B`, NF4 4-bit quantization, float16 compute, a 1024-token maximum decision response and non-thinking mode.

For a separately served compatible model:

```bash
export ELIA_BRAIN=openai_compatible
export ELIA_MODEL_BASE_URL=http://127.0.0.1:8000/v1
export ELIA_MODEL_ID=Qwen/Qwen3.5-9B
python -m elia --cycles 1
```

## Checkpoint handoff

Keep the secret outside GitHub:

```bash
export ELIA_CHECKPOINT_KEY='<long-random-secret>'
```

Export a clean state after stopping the active loop:

```bash
python -m elia --verify
python -m elia --checkpoint-export /path/to/elia-genesis.eliacp
```

Preserve the private archive and the printed digest separately. On a fresh machine, restore before loading the model:

```bash
python -m elia \
  --checkpoint-restore /path/to/elia-genesis.eliacp \
  --expected-checkpoint-digest <TRUSTED_DIGEST>
python -m elia --verify
python -m elia --status
```

See `runtime/kaggle/README.md` for the full Kaggle handoff protocol.

## State layout

By default:

```text
.elia/
├── memory.sqlite3          # memories, goals, metrics, scheduler/meta
├── chronicle.jsonl         # hash-chained history
├── checkpoint.anchor.json  # locally trusted checkpoint counter/digest
└── workspace/              # bounded private working files
```

`.elia/`, checkpoint archives, private memory and secrets must never be committed to this public repository.

## Research principles

1. **Identity is not model weights.** The model can be replaced.
2. **Memory must alter future behavior.** Logging alone is not memory.
3. **Goals must outlive a single inference.** Otherwise autonomy collapses back into prompting.
4. **Needs must be inspectable.** Self-maintenance pressure is derived from verified state.
5. **Autonomy must be observable.** Decisions, outcomes and goal changes are recorded.
6. **Resources are part of cognition.** GPU time is visible to the agent.
7. **Continuity must survive migration.** A single notebook VM is not an identity substrate.
8. **Claims require tests.** Continuity, autonomy and self-maintenance remain hypotheses until independently reproduced.
9. **Network access is not authority.** Public information may be read; access controls and private systems remain boundaries.

## What is proven by CI today

The zero-GPU regression suite currently exercises, among other things:

- restart continuity;
- Chronicle tamper detection;
- workspace jail enforcement;
- rejection of private-network HTTP targets;
- authenticated checkpoint round-trip to a fresh state directory;
- wrong-key and payload-tamper rejection;
- rollback rejection;
- durable goals across restarts and machine migration;
- evidence-gated goal completion;
- deterministic self-maintenance needs;
- persistent intended wake time.

CI does **not** prove consciousness, AGI, long-horizon autonomous survival or economic self-sufficiency. It proves specific runtime properties needed to test those stronger hypotheses honestly.

## Status

**Genesis v0.3:** persistent identity substrate, authenticated migration, durable goals, state-derived needs and scheduler intent are implemented. The next empirical blocker is a real Kaggle GPU run with Qwen3.5-9B and then repeated cross-session operation under the 30-hour weekly budget.
