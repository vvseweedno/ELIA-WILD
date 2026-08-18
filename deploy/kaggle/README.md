# Kaggle runtime

ELIA WILD treats Kaggle GPU time as a scarce energy budget. The runtime tracks both:

- active runtime/session wall time (`gpu_runtime_seconds`)
- actual model-call wall time (`brain_seconds`)

The configured weekly ceiling is 30 hours. Kaggle's own quota remains authoritative; the local ledger is an internal planning signal, not a replacement for platform accounting.

## 1. Prove the body without GPU

Before loading Qwen:

```bash
pip install -e '.[test]'
ELIA_BRAIN=mock pytest
ELIA_BRAIN=mock python -m elia --cycles 3
python -m elia --verify
python -m elia --status
```

Do not spend GPU quota until this path is green.

## 2. CPU-only autonomic preflight

Every normal launch now evaluates persistent state **before constructing the model backend**. You can inspect the decision explicitly:

```bash
python -m elia --preflight
```

The result is one of:

- `wake` — continuity is valid, local GPU budget remains and the intended wake time is due;
- `hibernate` — the intended wake time is still in the future or local weekly GPU budget is exhausted;
- `halt` — Chronicle integrity failed and normal cognition must not start.

`--force-wake` may bypass a future scheduler timestamp, but it cannot bypass Chronicle integrity or exhausted-budget guards.

This means a launcher can call ELIA frequently without paying the cost of loading Qwen every time.

## 3. Install the constrained-GPU brain

The default Genesis path loads Qwen directly with Transformers and bitsandbytes 4-bit quantization:

```bash
pip install -e '.[gpu]'
```

Default model configuration:

```text
backend        transformers_4bit
model          Qwen/Qwen3.5-9B
quantization   NF4 4-bit + double quant
compute dtype  float16
thinking       false
max output     1024 tokens
```

Run one real cognitive cycle first:

```bash
python -m elia --cycles 1
python -m elia --verify
python -m elia --status
```

The brain is lazy-loaded: reaching `EliaRuntime` or running preflight/status does not itself instantiate Qwen. The first actual `_think()` loads the configured model.

## 4. Hibernation instead of GPU sleep

`runtime.max_in_session_sleep_seconds` defaults to 5 seconds. If the cognitive cycle requests a longer delay, ELIA records a `HIBERNATE` Chronicle transition and exits instead of holding a Kaggle GPU while sleeping.

For Kaggle, configure an automatic authenticated checkpoint destination:

```bash
export ELIA_AUTO_CHECKPOINT_PATH=/kaggle/working/elia-genesis.eliacp
```

The checkpoint still requires the secret `ELIA_CHECKPOINT_KEY`. After a clean finite run or hibernation, the CLI exports the authenticated checkpoint automatically when both are configured.

This changes the intended lifecycle from:

```text
load Qwen -> think -> sleep on GPU -> think -> sleep on GPU
```

to:

```text
CPU preflight
    ↓ only if due
load Qwen -> think -> persist next wake -> HIBERNATE -> authenticated checkpoint -> release session
```

**Current boundary:** hibernation and the wake contract are implemented, but this repository does not yet independently relaunch a stopped Kaggle session. A separate wake transport/launcher is the next systems layer.

## 5. Alternate local API path

If a compatible vLLM/SGLang/Transformers server is already running:

```bash
export ELIA_BRAIN=openai_compatible
export ELIA_MODEL_BASE_URL=http://127.0.0.1:8000/v1
export ELIA_MODEL_ID=Qwen/Qwen3.5-9B
python -m elia --cycles 1
```

The model server is deliberately external to ELIA's identity state. Replacing the server or checkpoint must not erase continuity.

## 6. Durable state and authenticated handoff

`.elia/` contains the durable identity state:

```text
.elia/memory.sqlite3
.elia/chronicle.jsonl
.elia/workspace/
.elia/checkpoint.anchor.json
```

The public GitHub repository contains code only. Do **not** commit `.elia/`, checkpoint archives, private memory, or checkpoint secrets.

Create a long random secret once and store it in Kaggle Secrets as `ELIA_CHECKPOINT_KEY`. The same secret must be available in every session that exports, inspects, or restores ELIA state.

Before ending a healthy session manually:

```bash
python -m elia --verify
python -m elia --checkpoint-export /kaggle/working/elia-genesis.eliacp
```

The command prints a `digest`. Preserve two separate things:

1. `elia-genesis.eliacp` — private checkpoint payload; store it in a private persistence channel such as a private Kaggle Dataset or another private user-controlled store.
2. the printed checkpoint `digest` — trusted rollback anchor; keep it separately from the checkpoint itself.

The HMAC key is never placed in the archive. The checkpoint contains authenticated hashes of SQLite state, Chronicle, and workspace files.

### Fresh Kaggle session

Clone/install the repository, restore the same Kaggle secret, obtain the latest private checkpoint, then run **before loading Qwen**:

```bash
python -m elia \
  --checkpoint-restore /path/to/elia-genesis.eliacp \
  --expected-checkpoint-digest <TRUSTED_DIGEST>
python -m elia --verify
python -m elia --preflight
python -m elia --status
```

If preflight returns `wake`, start normal cognition:

```bash
python -m elia
```

On an existing machine, `.elia/checkpoint.anchor.json` rejects checkpoints older than the locally trusted counter. On a completely fresh machine there is no local history, so `--expected-checkpoint-digest` is the external fact that prevents a valid-but-old checkpoint from masquerading as the latest state.

If restore fails authentication, integrity, Chronicle, SQLite, identity-name, rollback, or expected-digest checks, **do not boot the model from that state**.

## 7. What a successful first GPU run proves

It proves only that:

1. Qwen loads in the available Kaggle accelerator memory.
2. One model decision can be parsed into ELIA's structured action schema.
3. The action executes through the bounded capability layer.
4. The result returns to persistent memory and capability health.
5. The Chronicle remains valid afterward.
6. The next intended wake time is persisted.
7. A long idle interval becomes hibernation rather than GPU sleep.
8. The resulting identity state can be checkpointed and authenticated for the next session.

It does **not** yet prove long-term autonomy, economic self-maintenance, identity continuity over long horizons, independent wake-up transport, or unattended survival.
