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

## 2. Install the constrained-GPU brain

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

Only then increase the number of cycles.

## 3. Alternate local API path

If a compatible vLLM/SGLang/Transformers server is already running:

```bash
export ELIA_BRAIN=openai_compatible
export ELIA_MODEL_BASE_URL=http://127.0.0.1:8000/v1
export ELIA_MODEL_ID=Qwen/Qwen3.5-9B
python -m elia --cycles 1
```

The model server is deliberately external to ELIA's identity state. Replacing the server or checkpoint must not erase continuity.

## 4. Persistence is the current hard boundary

`.elia/` contains the durable state:

```text
.elia/memory.sqlite3
.elia/chronicle.jsonl
.elia/workspace/
```

Kaggle session storage alone is not enough for continuity across independent sessions. Until Genesis v0.2 implements authenticated checkpoint/restore, losing `.elia/` is considered a continuity failure rather than a normal restart.

## 5. What a successful first GPU run proves

It proves only that:

1. Qwen loads in the available Kaggle accelerator memory.
2. One model decision can be parsed into ELIA's structured action schema.
3. The action executes through the bounded tool layer.
4. The result returns to persistent memory.
5. The Chronicle remains valid afterward.

It does **not** yet prove long-term autonomy, economic self-maintenance, identity continuity across machines, or unattended multi-session survival.
