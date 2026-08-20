# ELIA WILD on Kaggle

Kaggle is a bounded compute organ for ELIA, not the identity store. Identity, lineage,
memory, resource state and authority survive outside any one GPU session through the
continuity checkpoint path.

## Production path

```text
private encrypted state Dataset
        ↓
CPU restore + digest/identity/Chronicle verification
        ↓
preflight: halt | hibernate | wake
        ↓ wake only
private T4 kernel + pinned ELIA ref
        ↓
bounded Qwen cognition
        ↓
accepted transition + hibernate
        ↓
XChaCha20-Poly1305 encrypted checkpoint
        ↓
relay verifies digest/counter/nonce
        ↓
new private Dataset version
```

The local Executive budget is a planning constraint; Kaggle's actual quota remains
authoritative.

## Required secrets

Create two independent secrets and keep them outside Git, checkpoints and Dataset
metadata:

- `ELIA_CHECKPOINT_KEY` — HMAC/authentication key, at least 16 bytes; use a long random
  value.
- `ELIA_CHECKPOINT_ENCRYPTION_KEY` — **base64 encoding of exactly 32 random bytes** for
  the XChaCha20-Poly1305 envelope.

Example local generation:

```bash
python - <<'PY'
import base64, secrets
print('ELIA_CHECKPOINT_KEY=' + secrets.token_urlsafe(48))
print('ELIA_CHECKPOINT_ENCRYPTION_KEY=' + base64.b64encode(secrets.token_bytes(32)).decode())
PY
```

For the manual/controlled notebook, attach both labels in Kaggle **Add-ons → Secrets**.
For the unattended relay, attach both labels once to the persistent private kernel ID
configured as `ELIA_KAGGLE_KERNEL`. Kaggle's current kernel metadata supports datasets,
GPU and internet settings but does not attach user secrets; secret attachment is a
one-time deployment property of that kernel.

## First launch notebook

Use `runtime/kaggle/ELIA_WILD_Genesis.ipynb` with a T4 GPU and Internet enabled. It is
fail-closed and performs:

1. clone the pinned consolidation ref;
2. require and validate both continuity secrets;
3. restore one attached encrypted ELIA state bundle, or create fresh Genesis state;
4. run `elia-doctor`, `elia-vitals`, Chronicle verification, status and supervisor
   dry-run before loading a model;
5. prove a real CUDA operation works;
6. install the pinned 4-bit Qwen backend;
7. run one bounded real cognition cycle;
8. verify accepted state;
9. export an encrypted checkpoint and trusted digest;
10. restore that checkpoint into an independent state directory and verify it again.

A failed gate stops the notebook. There is no plaintext fallback.

## Bootstrap the private state Dataset

On an authorized machine with Kaggle CLI configured:

```bash
export ELIA_CHECKPOINT_KEY='...'
export ELIA_CHECKPOINT_ENCRYPTION_KEY='...base64...'
export KAGGLE_API_TOKEN='...'

python scripts/bootstrap_kaggle_state.py \
  --dataset <owner>/elia-wild-state \
  --output .bootstrap/elia-wild-state \
  --create-dataset
```

The bootstrap script requires encryption and strips both ELIA keys from the environment
passed to the external `kaggle` CLI process.

## Unattended wake relay

Configure the same authorized relay host:

```bash
export KAGGLE_API_TOKEN='...'
export ELIA_CHECKPOINT_KEY='...'
export ELIA_CHECKPOINT_ENCRYPTION_KEY='...base64...'
export ELIA_KAGGLE_STATE_DATASET='<owner>/elia-wild-state'
export ELIA_KAGGLE_KERNEL='<owner>/elia-wild-runtime'
export ELIA_REPO_REF='elia/genesis-1.7.1-consolidation'

python scripts/kaggle_wake.py
```

The relay is idempotent around a persisted pending launch nonce. It:

- rejects plaintext checkpoints;
- verifies the trusted digest before restore;
- does not launch GPU cognition when preflight says hibernate/halt;
- records a pending launch before pushing a kernel;
- accepts output only when nonce, source digest, checkpoint digest/counter and encrypted
  envelope agree;
- suppresses additional launches after repeated failures;
- versions the private state Dataset only after successful verification.

The Kaggle CLI child receives its own API credential but not ELIA continuity keys. The
remote kernel reads the two continuity secrets from its attached Kaggle Secrets.

## Hibernation

ELIA does not intentionally hold paid/scarce GPU time while sleeping. Long waits become
persistent hibernation: next-wake state is recorded, an encrypted checkpoint is emitted,
and the session may terminate. A later authorized heartbeat invokes the relay again.

## Fresh-machine restore contract

A fresh machine has no local rollback anchor, so restore requires the trusted digest:

```bash
export ELIA_CHECKPOINT_REQUIRE_ENCRYPTION=1
python -m elia \
  --checkpoint-restore /path/to/elia-genesis.eliacp \
  --expected-checkpoint-digest <TRUSTED_DIGEST>
python -m elia --verify
python -m elia --preflight
```

The checkpoint manager validates AEAD authentication, HMAC/manifest hashes, member and
size limits, SQLite integrity, Chronicle head, identity fingerprint and rollback/fork
rules before promoting restored state.

## What this proves — and what it does not

A successful end-to-end run proves that this implementation can install, restore a
verified identity state, execute a real model cycle on Kaggle, persist the accepted
transition in encrypted form and restore it again. Repeated successful relayed runs can
then measure continuity and autonomous operation over time.

It does not by itself prove consciousness, AGI, economic self-sufficiency or indefinite
survival. Those are empirical claims requiring longitudinal evidence, not deployment
labels.
