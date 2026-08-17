# ELIA WILD

**A persistent autonomous-identity experiment under a hard compute budget.**

ELIA WILD asks a falsifiable systems question:

> Can an artificial identity preserve continuity across model calls and machines, derive needs from verified state, maintain durable goals, diagnose its own runtime, decide when expensive cognition is worth waking, and continue across externally relaunched compute sessions without a human supplying every next task?

The language model is replaceable. The persistent agent is the larger system: memory, goals, Chronicle, resource ledger, scheduler, capability health, authenticated checkpoints, lifecycle guards, wake transport and bounded external authority.

## Current architecture — Genesis v0.6

```text
private authenticated state
        ↓
external hourly heartbeat (cheap CPU)
        ↓
verify HMAC + digest + Chronicle + SQLite
        ↓
CPU-only lifecycle preflight
        ├── halt      → preserve evidence, never load model
        ├── hibernate → no GPU launch
        └── wake
              ↓
        private Kaggle state relay + launch nonce
              ↓
        bounded T4 kernel launch
              ↓
        restore + verify again inside Kaggle
              ↓
        lazy-load Qwen3.5-9B 4-bit
              ↓
        observe → needs → goals → one action → outcome
              ↓
        memory + capability health + Chronicle
              ↓
        persist next wake intention
              ↓
        HIBERNATE instead of sleeping on GPU
              ↓
        authenticated checkpoint + relay report
              ↓
next heartbeat validates and accepts state
```

The design separates four things:

- **brain** — replaceable inference model;
- **identity state** — memory, goals, Chronicle, scheduler and resource ledger;
- **authority** — bounded capabilities that determine which actions actually exist;
- **lifecycle** — deterministic logic deciding whether cognition should wake at all.

A smarter model does not automatically receive broader authority.

## Default brain and compute envelope

- Model: `Qwen/Qwen3.5-9B`
- Backend: direct Transformers + bitsandbytes NF4 4-bit
- Alternate backend: local OpenAI-compatible endpoint
- Default remote accelerator: `NvidiaTeslaT4`
- Weekly internal GPU budget: **30 hours**
- Default external wake burst ceiling: **3600 seconds**
- Thinking: disabled by default
- Maximum model decision response: 1024 tokens
- Durable memory: external to model context
- Brain loading: lazy, only after deterministic preflight says `wake`

## Implemented autonomy substrate

### Persistent memory and goals

SQLite stores memories, runtime metadata, GPU/brain metrics, durable goals, goal events and capability execution history. Goals survive model calls, process restarts and authenticated machine migration.

Goal completion/abandonment requires evidence. Duplicate active goals are suppressed and the active set is bounded.

### State-derived needs

The runtime derives explicit maintenance pressures from observable state instead of allowing the model to invent hidden drives. Signals currently include:

- Chronicle integrity failure;
- missing authenticated checkpoint;
- low/exhausted GPU runway;
- recent runtime errors;
- missing or fully blocked goals;
- repeatedly degraded capabilities.

### Capability awareness and bounded self-repair

Every declared capability has structured metadata describing authority, side effects, network scope, cost and enablement. Execution history records success/failure, latency and consecutive failure streaks.

After three real consecutive failures, ordinary capabilities are suppressed instead of being blindly retried. ELIA can run a bounded `self_check` and persist a `propose_repair` artifact, but repair proposals do not silently deploy themselves.

Current authority includes:

- `noop`
- private workspace list/read/write inside a path jail
- public HTTP/HTTPS GET with non-public destinations rejected
- bounded local `self_check`
- proposal-only repair staging

There is no unrestricted shell, credential harvesting, arbitrary third-party writes, hidden persistence or uncontrolled replication.

### Tamper-evident Chronicle

`chronicle.jsonl` is an append-only SHA-256 hash chain. Modification of recorded history is detectable at boot, preflight and manual verification.

### Authenticated checkpoint migration

Versioned `.eliacp` checkpoints contain a consistent SQLite snapshot, Chronicle, private workspace and identity metadata. The manifest carries per-file hashes and is authenticated with an HMAC key kept outside the archive and public repository.

Restore verifies HMAC, expected digest, checkpoint counter, file integrity, SQLite integrity, Chronicle integrity/head and identity name before normal cognition starts. Local anchors and fresh-machine trusted digests detect rollback.

### Scheduler and hibernation

Each cycle persists `next_wake_at`. Long requested sleeps do not call `sleep()` while a scarce GPU is allocated. The runtime writes a `HIBERNATE` Chronicle transition, optionally exports an authenticated checkpoint and exits.

### CPU-only autonomic preflight

`python -m elia --preflight` returns:

- `wake` — cognition is due and budget remains;
- `hibernate` — future wake time or exhausted local GPU budget;
- `halt` — continuity integrity failed.

`--force-wake` may bypass schedule timing only. It cannot bypass integrity or budget guards.

### External wake relay

Genesis v0.6 adds a guarded GitHub Actions heartbeat and Kaggle relay:

1. download the latest **private** ELIA state Dataset;
2. authenticate and restore it on cheap CPU;
3. run preflight;
4. launch no GPU unless preflight says `wake`;
5. record a unique pending launch nonce before launch;
6. wait until the private state Dataset version is ready;
7. start one private T4 Kaggle script kernel with a bounded timeout;
8. let the kernel retrieve `ELIA_CHECKPOINT_KEY` from Kaggle Secrets;
9. restore and verify state again before loading Qwen;
10. accept kernel output only when nonce, source digest, output digest, HMAC, counter, identity and Chronicle agree;
11. preserve the last trusted state on any failure;
12. suppress new GPU launches after three consecutive kernel/relay failures.

The heartbeat does not remain alive while Kaggle runs. A later heartbeat observes the pending kernel and either waits, accepts validated output or records failure.

The scheduled workflow exists but is **disabled by default** until the required private Kaggle resources and secrets are configured.

See `docs/WAKE_TRANSPORT.md`.

## Quick start — zero GPU

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'

ELIA_BRAIN=mock pytest
ELIA_BRAIN=mock python -m elia --cycles 3
python -m elia --verify
python -m elia --preflight
python -m elia --status
```

## Direct Kaggle/Qwen smoke test

```bash
pip install -e '.[gpu]'
python -m elia --preflight
python -m elia --cycles 1
python -m elia --verify
python -m elia --status
```

## Bootstrap the private wake state without GPU

```bash
export ELIA_CHECKPOINT_KEY='<long-random-secret>'
python scripts/bootstrap_kaggle_state.py \
  --dataset OWNER/elia-wild-state \
  --output .bootstrap/elia-wild-state
```

With authenticated Kaggle CLI access, `--create-dataset` can create the initial private Dataset. Never commit the checkpoint, digest transport state or secrets.

## State layout

```text
.elia/
├── memory.sqlite3
├── chronicle.jsonl
├── checkpoint.anchor.json
└── workspace/
```

External private relay state:

```text
elia-genesis.eliacp
trusted-digest.txt
transport-state.json
```

All are ignored by Git and must remain outside the public repository.

## Research principles

1. **Identity is not model weights.**
2. **Memory must alter future behavior.**
3. **Goals must outlive a single inference.**
4. **Needs must be inspectable.**
5. **Authority must be explicit.**
6. **Failures must become state, not blind retries.**
7. **Resources are part of cognition.**
8. **Sleeping must release expensive compute.**
9. **Continuity must survive machine migration.**
10. **External wake-up must authenticate state before cognition.**
11. **Claims require reproducible tests.**

## What CI proves today

The zero-GPU regression suite exercises restart continuity, Chronicle tamper detection, workspace/network boundaries, authenticated checkpoint migration and rollback rejection, persistent goals and scheduler intent, evidence-gated goal lifecycle, deterministic needs, capability health/degradation, self-check cleanup/history, lazy model loading, lifecycle preflight/hibernation, auto-checkpointing, wake transport state/nonce validation, T4 kernel metadata, real runner-template compilation and zero-GPU private-state bootstrap.

CI does **not** prove consciousness, AGI, long-horizon survival, economic self-sufficiency or a successful real Kaggle/Qwen relay. Those remain empirical questions.

## Status

**Genesis v0.6 software path is implemented and regression-tested.** The remaining v0.6 gate is external activation: create/configure the user's private Kaggle state Dataset, private kernel, Kaggle Secret and GitHub secrets/variables, then observe a real T4 wake → cognition → hibernate → relay cycle before calling the milestone experimentally complete.
