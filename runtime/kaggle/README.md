# Kaggle runtime notes

ELIA WILD treats Kaggle GPU time as a scarce energy budget. The runtime therefore tracks both:

- active runtime/session wall time (`gpu_runtime_seconds`)
- actual model-call wall time (`brain_seconds`)

The configured weekly ceiling is 30 hours. Kaggle's own quota remains authoritative; the local ledger is an internal planning signal, not a replacement for platform accounting.

## Model service

The ELIA runtime talks to an OpenAI-compatible endpoint. Qwen3.5 officially supports this interface through vLLM, SGLang, and Transformers serving.

For a compatible environment, start a text-only Qwen server separately and then point ELIA at it:

```bash
export ELIA_BRAIN=openai_compatible
export ELIA_MODEL_BASE_URL=http://127.0.0.1:8000/v1
export ELIA_MODEL_ID=Qwen/Qwen3.5-9B
```

On a 16 GB accelerator, the full 9B checkpoint may not leave enough memory for a useful KV cache. Use a compatible 4-bit quantization or another tested serving path rather than silently shrinking the identity/memory layer. The model identifier is deliberately configurable for this reason.

## First smoke run

Always prove the runtime before spending GPU quota:

```bash
pip install -e '.[test]'
ELIA_BRAIN=mock pytest
ELIA_BRAIN=mock python -m elia --cycles 3
python -m elia --verify
python -m elia --status
```

Only after the smoke path passes should a GPU model server be started.

## Persistence warning

Kaggle session storage is not a sufficient identity store by itself. `.elia/` contains the durable state and must be restored into the next session from a persistent external location. Genesis v0.1 intentionally does not yet automate that handoff; losing `.elia/` is treated as loss of continuity, not a normal restart.
