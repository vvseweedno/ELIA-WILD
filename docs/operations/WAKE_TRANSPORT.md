# Genesis v0.6 — External Wake Transport

ELIA can already persist a next-wake intention and hibernate instead of sleeping on a scarce GPU. This layer closes the next systems gap: a small external heartbeat can decide whether to wake Kaggle, relay authenticated state, and leave the expensive model offline when cognition is not due.

## Architecture

```text
GitHub Actions heartbeat (CPU, short-lived)
        ↓
download private Kaggle state Dataset
        ↓
HMAC + digest + Chronicle + SQLite verification
        ↓
CPU-only ELIA preflight
        ├── hibernate → stop heartbeat, no GPU
        ├── halt      → stop, preserve last trusted state
        └── wake
              ↓
        write pending launch nonce to private Dataset
              ↓
        kaggle kernels push → private T4 script kernel
              ↓
        runner retrieves ELIA_CHECKPOINT_KEY from Kaggle Secrets
              ↓
        restore + verify + preflight before Qwen load
              ↓
        Qwen cognitive burst (bounded max cycles)
              ↓
        HIBERNATE / finite pause
              ↓
        authenticated checkpoint + trusted digest + relay report
              ↓
next GitHub heartbeat validates output
              ↓
private state Dataset receives a new version
```

The heartbeat does not remain alive while Kaggle runs. `transport-state.json` carries the pending launch nonce between heartbeats, so later runs can determine whether they are waiting for a kernel, accepting completed output, recording a failure, or launching the next wake.

## Repository locations

- Kaggle deployment template, notebook and notes: `deploy/kaggle/`
- GitHub heartbeat implementation: `scripts/kaggle/wake.py`
- first-state bootstrap helper: `scripts/kaggle/bootstrap_state.py`
- scheduled relay workflow: `.github/workflows/wake.yml`

## External resources

Create these once in the user's own accounts:

### Kaggle

- one **private Dataset** for ELIA state, for example `OWNER/elia-wild-state`;
- one **private script kernel** id, for example `OWNER/elia-wild-genesis`;
- one Kaggle Secret named exactly `ELIA_CHECKPOINT_KEY` and attached/available to that kernel.

The private Dataset contains:

```text
elia-genesis.eliacp
trusted-digest.txt
transport-state.json
dataset-metadata.json
```

It never contains `ELIA_CHECKPOINT_KEY`.

### GitHub repository secrets

Configure:

```text
KAGGLE_API_TOKEN
ELIA_CHECKPOINT_KEY
```

`KAGGLE_API_TOKEN` lets the external heartbeat operate the user's Kaggle Dataset/kernel through the official CLI. `ELIA_CHECKPOINT_KEY` lets the GitHub heartbeat authenticate state before it decides whether to spend GPU time.

### GitHub repository variables

Configure:

```text
ELIA_KAGGLE_STATE_DATASET = OWNER/elia-wild-state
ELIA_KAGGLE_KERNEL        = OWNER/elia-wild-genesis
ELIA_KAGGLE_ACCELERATOR   = NvidiaTeslaT4
ELIA_WAKE_MAX_CYCLES      = 8
ELIA_WAKE_ENABLED         = false
```

The scheduled workflow is present in `.github/workflows/wake.yml`, but its job only runs when `ELIA_WAKE_ENABLED` is exactly `true`. Keep it false while bootstrapping.

## Bootstrap the first state without GPU

Set the checkpoint secret locally or in a protected CI environment:

```bash
export ELIA_CHECKPOINT_KEY='<long-random-secret>'
python scripts/kaggle/bootstrap_state.py \
  --dataset OWNER/elia-wild-state \
  --output .bootstrap/elia-wild-state
```

Inspect the output before upload. The bootstrap creates checkpoint counter 1, a trusted digest, transport state, and private-dataset metadata. It does not load Qwen.

If the official Kaggle CLI is authenticated with `KAGGLE_API_TOKEN`, the same script can create the initial private Dataset:

```bash
python scripts/kaggle/bootstrap_state.py \
  --dataset OWNER/elia-wild-state \
  --output .bootstrap/elia-wild-state \
  --create-dataset
```

Do not commit `.bootstrap/`, checkpoint payloads, API tokens, or secret values.

## Kernel secret boundary

The generated Kaggle runner contains only non-secret launch data:

- launch nonce;
- source checkpoint digest;
- pinned ELIA repository commit;
- maximum cycles;
- public repository URL.

At runtime it obtains `ELIA_CHECKPOINT_KEY` through `kaggle_secrets.UserSecretsClient`. If the secret is absent, inaccessible, or too short, the runner fails before restoring state or loading Qwen.

The secret must never be interpolated into generated Python source, kernel metadata, relay reports, Dataset files, Chronicle entries, or GitHub logs.

## Wake heartbeat semantics

Every enabled heartbeat is intentionally short.

If no kernel is pending, it authenticates the latest checkpoint, restores it into an ephemeral GitHub runner directory, and calls deterministic CPU-only preflight. It launches Kaggle only on `wake`.

Before launch it versions the private state Dataset with a fresh `pending_launch_nonce`. The generated runner carries the same nonce. This prevents output from an older or unrelated kernel run from being accepted as current identity state.

If a kernel is queued or running, the heartbeat exits.

If it completes, the heartbeat downloads output and accepts the new checkpoint only after all of these match:

```text
pending launch nonce
source digest
relay output digest
HMAC authentication
checkpoint counter
identity name
Chronicle integrity/head
SQLite integrity
```

Only then is the private state Dataset versioned with the new checkpoint.

If the kernel fails, output is invalid, or a pending launch times out, the last trusted checkpoint stays untouched. The transport failure count increments. After three consecutive failures, automatic GPU launches are suppressed until the fault is diagnosed; heartbeat activity itself remains safe to run.

## T4 default

Genesis v0.6 uses `NvidiaTeslaT4` as its default `machine_shape`. This is a platform compatibility choice rather than a model preference. Change it only after validating the target accelerator with the current Kaggle image and Qwen runtime.

## Enabling autonomous wake

Do not enable the schedule until all of the following are true:

```text
private state Dataset exists
Kaggle kernel id is reserved/usable
ELIA_CHECKPOINT_KEY exists in both GitHub Secrets and Kaggle Secrets
KAGGLE_API_TOKEN works in GitHub Actions
ELIA_KAGGLE_STATE_DATASET is correct
ELIA_KAGGLE_KERNEL is correct
manual workflow_dispatch heartbeat succeeds without launching unexpectedly
```

Then set:

```text
ELIA_WAKE_ENABLED = true
```

The workflow runs at minute 17 of every hour. If ELIA's persisted wake time is still in the future, or its local GPU budget is exhausted, the heartbeat ends without starting a GPU kernel.

## Current boundary

The relay can maintain ELIA's own authenticated state and start its own configured Kaggle kernel. It does not grant broad Kaggle account control, arbitrary remote code execution on third-party infrastructure, credential discovery, self-propagation, or authority outside the explicitly configured Dataset/kernel.
