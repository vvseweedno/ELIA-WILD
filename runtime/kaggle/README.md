# ELIA WILD on Kaggle

Kaggle is a bounded compute organ for ELIA, not the identity store and not the rollback witness. Identity, lineage, memory, durable agency and authority survive outside any one GPU session through encrypted continuity state plus an independent external witness.

## Production path

```text
private encrypted state Dataset
        ↓
independent rollback/fork witness restored from GitHub Actions artifact
        ↓
CPU restore + digest/identity/Chronicle verification
        ↓
preflight: halt | hibernate | wake
        ↓ wake only
private Kaggle GPU kernel + pinned ELIA ref
        ↓
bounded Qwen cognition
        ↓
accepted transition + deterministic next-wake policy
        ↓
XChaCha20-Poly1305 encrypted checkpoint
        ↓
relay verifies nonce + source digest + output digest/counter
        ↓
new private Dataset version becomes durable
        ↓
independent witness advances
        ↓
new immutable GitHub Actions witness artifact
```

The local Executive budget is a planning constraint; Kaggle's actual quota remains authoritative.

## Required secrets

Create independent secrets and keep them outside Git, checkpoint metadata and Dataset metadata:

- `ELIA_CHECKPOINT_KEY` — HMAC/authentication key, at least 16 bytes; use a long random value.
- `ELIA_CHECKPOINT_ENCRYPTION_KEY` — **base64 encoding of exactly 32 random bytes** for the XChaCha20-Poly1305 envelope.
- `KAGGLE_API_TOKEN` — relay credential for the Kaggle CLI/API.

Example continuity-key generation:

```bash
python - <<'PY'
import base64, secrets
print('ELIA_CHECKPOINT_KEY=' + secrets.token_urlsafe(48))
print('ELIA_CHECKPOINT_ENCRYPTION_KEY=' + base64.b64encode(secrets.token_bytes(32)).decode())
PY
```

For the controlled notebook, attach the two ELIA continuity secrets in Kaggle **Add-ons → Secrets**. For the persistent relay kernel, attach the same two labels once to the private kernel ID configured by the wake relay. Kaggle kernel metadata configures datasets/GPU/internet but does not itself grant ELIA continuity secrets.

## First controlled launch

Use `runtime/kaggle/ELIA_WILD_Genesis.ipynb` with Internet and a supported NVIDIA GPU enabled. The notebook is fail-closed and performs:

1. require `ELIA_REPO_REF` to be an immutable 40-hex commit SHA and check it out detached;
2. require and validate both continuity secrets;
3. restore one attached encrypted ELIA state bundle, or create fresh Genesis state;
4. run `elia-doctor`, `elia-vitals`, Chronicle verification, status and supervisor dry-run before model loading;
5. prove a real CUDA operation works;
6. install the pinned 4-bit Qwen backend;
7. run one bounded real cognition cycle through `ELIARuntime`;
8. verify accepted state;
9. export an encrypted checkpoint and trusted digest;
10. restore that checkpoint into an independent state directory and verify it again.

Any failed gate stops the launch. There is no plaintext continuity fallback.

## Bootstrap private state and witness

On an authorized machine with the Kaggle CLI configured:

```bash
export ELIA_CHECKPOINT_KEY='...'
export ELIA_CHECKPOINT_ENCRYPTION_KEY='...base64...'
export KAGGLE_API_TOKEN='...'

python scripts/bootstrap_kaggle_state.py \
  --dataset <owner>/elia-wild-state \
  --output .bootstrap/elia-wild-state \
  --create-dataset
```

Bootstrap requires encryption and creates the initial authenticated rollback witness outside the Dataset. The external `kaggle` child process receives the Kaggle credential but not ELIA continuity keys.

For GitHub Actions operation, the first witness artifact must be seeded by an **explicit `workflow_dispatch`** after deployment variables/secrets are configured. A scheduled heartbeat must not create a new witness by trusting the Dataset it is supposed to verify.

## GitHub Actions heartbeat

`.github/workflows/wake.yml` is the low-cost external heartbeat. It is intentionally not continuous resident compute: it runs hourly and only when repository variable:

```text
ELIA_WAKE_ENABLED=true
```

is set.

Required repository **Secrets**:

```text
KAGGLE_API_TOKEN
ELIA_CHECKPOINT_KEY
ELIA_CHECKPOINT_ENCRYPTION_KEY
ELIA_WAKE_RESET_AUTH             # independent random value, at least 32 characters
```

Required/expected repository **Variables**:

```text
ELIA_WAKE_ENABLED=true
ELIA_KAGGLE_STATE_DATASET=<owner>/elia-wild-state
ELIA_KAGGLE_KERNEL=<owner>/elia-wild-runtime
ELIA_KAGGLE_ACCELERATOR=NvidiaTeslaT4   # or validated equivalent
ELIA_KAGGLE_KERNEL_TIMEOUT=3600         # optional bounded override
ELIA_WAKE_MAX_CYCLES=8                  # optional bounded override
```

Each heartbeat:

1. checks out the exact source revision;
2. restores the latest independent witness emitted by this workflow on the default
   branch of this repository;
3. refuses scheduled operation if no trusted witness exists;
4. exposes ELIA/Kaggle secrets only to the relay step, not checkout/install steps;
5. downloads and verifies encrypted Kaggle state against the independent witness;
6. performs deterministic preflight;
7. launches no GPU when mode is `hibernate` or `halt`;
8. if wake is required, records pending intent before pushing the Kaggle kernel;
9. on a later heartbeat, accepts completed output only when nonce/digests/counters/encryption agree;
10. advances the Dataset first and the independent witness only after durable acceptance;
11. uploads the witness as a 90-day immutable Actions artifact even when a later relay
    operation fails, then propagates the relay failure to the workflow result.

The external witness is intentionally separate from the state Dataset. If the witness is missing, stale, tampered with or disagrees with the Dataset, the relay fails closed. Availability is sacrificed rather than allowing the potentially replayed Dataset to declare itself current.

## Wake relay invariants

`scripts/kaggle_wake.py` is idempotent around persisted pending launch state. It:

- rejects plaintext external checkpoints;
- validates authenticated/encrypted restore before preflight;
- checks source checkpoint counter/digest against the independent witness;
- avoids duplicate GPU launches while a prior kernel is queued/running;
- validates a completed relay report against its launch nonce and source digest;
- requires output checkpoint metadata to match the relay report;
- requires cognition-started runs to advance the authenticated checkpoint counter;
- records bounded failures and suppresses repeated launches after the failure threshold;
- authenticates pending/failure/reset transport state and refuses unsigned legacy state;
- returns non-zero status for rejected output, unknown/failed transport, push failure or
  an open circuit instead of reporting a false-green heartbeat;
- versions private state only after verification;
- advances the independent witness only after the new Dataset version is durable.

The Kaggle CLI child does not receive ELIA continuity keys. The remote kernel reads continuity secrets from its private Kaggle Secrets attachment.

After three consecutive failures the circuit remains open. Diagnose the incident, then
run `workflow_dispatch` on the default branch with `reset_circuit=true` and a concrete
`reset_reason`. The reset requires `ELIA_WAKE_RESET_AUTH`, refuses to clear a pending
launch, and stores a timestamped reset count plus a hash of the operator evidence.

## Hibernation

ELIA does not intentionally hold scarce GPU time while sleeping. Long waits become persistent hibernation: the next-wake policy is stored, an encrypted checkpoint is emitted, and the GPU session may terminate.

Agency owns a one-way wake ceiling: a model can request an earlier wake but cannot postpone verified maintenance or unfinished work beyond the deterministic deadline. The external GitHub heartbeat is currently hourly, so sub-hour internal deadlines mean “wake on the next available heartbeat,” not fictitious sub-hour scheduling.

## Fresh-machine restore

A fresh machine without a local checkpoint anchor must receive a trusted digest or independent witness through a separate trusted channel:

```bash
export ELIA_CHECKPOINT_REQUIRE_ENCRYPTION=1
python -m elia \
  --checkpoint-restore /path/to/elia-genesis.eliacp \
  --expected-checkpoint-digest <TRUSTED_DIGEST>
python -m elia --verify
python -m elia --preflight
```

Restore validates AEAD authentication, HMAC/manifest hashes, archive size/member bounds, SQLite integrity, Chronicle state, identity fingerprint and rollback/fork rules before promoting state.

## Deployment proof still required

Software CI verifies the relay logic, encryption, witness behavior, lifecycle and clean installation. It does **not** prove the selected real Kaggle accelerator can load the pinned Qwen substrate or that the unattended round trip works under live platform conditions.

The remaining empirical gate for Genesis 1.7.1 is:

```text
real GPU load
→ one bounded cognitive cycle
→ encrypted accepted checkpoint
→ independent restore
→ hibernate
→ GitHub heartbeat restores witness
→ Kaggle relay wakes later
→ authenticated checkpoint counter advances
```

Passing that gate would prove this implementation can perform a real persistent wake/hibernate/relaunch cycle on the chosen infrastructure. It would not prove consciousness, AGI, economic self-sufficiency or indefinite survival.
