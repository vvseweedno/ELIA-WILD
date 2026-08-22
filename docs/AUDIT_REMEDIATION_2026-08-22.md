# ELIA WILD Genesis 1.7.1 — Audit Remediation Record

**Date:** 2026-08-22
**Repository:** `vvseweedno/ELIA-WILD`
**Remediated branch:** PR #19, `elia/genesis-1.7.1-consolidation`
**Pre-remediation head:** `19bda9305d27537223456e5052444e02b5b529d4`
**Scope:** whole-organism architecture, continuity, authority, security, privacy,
mathematics, ML/runtime compatibility, fault recovery, typing, packaging, supply
chain and deployment truthfulness.

## Verdict

The audited tree now has one materially stronger truth kernel rather than another
layer of persona or symbolic anatomy. The implemented software path has explicit
accepted-state, authority, integrity, resource and provider boundaries, and the
known code-confirmed counterexamples found in this audit have regression tests.

This is a release-candidate verdict for a persistent autonomous-agent/identity
research runtime. It is not a claim that every future defect is impossible, that a
hostile root cannot rewrite local evidence, or that the unexecuted live Kaggle/GPU
path has already succeeded. The real Qwen/T4 round trip remains the release's
empirical deployment gate.

## Post-audit licensing and ownership control

After technical consolidation, version `1.7.1a2` replaced the repository's MIT
terms with the ELIA WILD Source-Available Proprietary License 1.0. The current
source remains publicly inspectable and installable in unmodified form solely
for pre-purchase inspection; execution, deployment, modification,
redistribution and other use require a separate paid written license. This
change is prospective: it does not purport to revoke rights validly granted with
earlier versions. Repository-wide `CODEOWNERS` assigns the canonical tree to
`@vvseweedno`, and the contribution policy rejects unsolicited changes unless a
prior written contributor agreement exists.

## Review team

The audit was split across the roles normally involved in an autonomous-agent
system and then integrated against one invariant model:

- whole-organism and continuity/distributed-systems architecture;
- application security, defensive red team, privacy and provider boundaries;
- authority, owner control and external-effect safety;
- mathematical accounting, statistics, calibration and ML evaluation;
- chaos, crash consistency, filesystem and concurrency testing;
- Python type/correctness and API-contract review;
- packaging, reproducible builds, CI and supply-chain provenance;
- model/runtime compatibility for pinned Qwen, Transformers and Kaggle T4;
- final cross-subsystem integration and claims review.

## Authoritative invariants

The remediation treats the following relations as non-negotiable:

```text
history != command
estimate != resource
submission != acceptance != verified payment
model output != authority
tool output != verified fact
configured sandbox != machine-attested isolation
internally valid history != externally anchored ancestry
software regression != live deployment evidence
```

Integrity, authority and accounting identities use exact finite data. Unknown
objects, non-string keys, cycles, `NaN`, infinity and lossy `str()` coercions cannot
silently enter a signature, idempotency key or accepted-state fingerprint.

## Implemented remediation

### Continuity and crash consistency

- A reentrant cross-process `StateWriterLock` serializes restore recovery, durable
  store construction, boot recovery and runtime pipeline publication. Checkpoint,
  restore and owner-control writers use the same lock ordering.
- `AcceptedTransitionGuard` now snapshots and restores SQLite, Chronicle and a
  bounded regular-file workspace, including file modes and empty directories.
  Symlinks, hardlinks, special files, mutation during snapshot and oversized trees
  fail closed.
- Checkpoint export is staged, journaled, fsync'd and ordered as artifact, metadata,
  then authenticated anchor. Restore requires the exact trusted predecessor and
  counter, uses a journal, and retains external-safety truth across rollback.
- CRC v2 binds identity, branch, checkpoint lineage, exact Chronicle prefix and
  strictly validated finite capsule data while preserving the versioned v1/v2 byte
  encoding. Legacy v1 state has an explicit upgrade path.
- Identity YAML, identity/lineage fingerprints and Chronicle append/read paths reject
  non-finite values, non-string keys, duplicate JSON names, implicit string coercion,
  schema drift and exact-type substitutions such as `true` for sequence `1`.
- Branch, lifecycle and supervisor recovery run before a dirty suffix can become a
  new trusted baseline. The supervisor has singleton/process-group discipline,
  bounded child handling and kill polling.

The accepted workspace contract is deliberately POSIX and regular-file-only. It
does not promise restoration of ownership, ACLs, xattrs, sparse layout or metadata
that an old archive never recorded.

### Authority, security and privacy

- Untrusted model decisions must be one bounded duplicate-free, finite, strictly
  typed JSON object. Invalid output becomes `noop`.
- The final system and user prompts are scrubbed at the outbound boundary. Remote
  model transport requires HTTPS except for literal loopback endpoints; responses
  are streamed under byte limits.
- Provider context is default-deny and projection-based. Raw memory, world, self,
  sensor, resource, work-port and local-database details do not cross the boundary
  merely because they exist in runtime state.
- Every declared external-I/O action rechecks owner kill, revocation and delegation
  lease at point of effect. Approval is not inferred from model intent or an earlier
  preflight.
- Owner signal/database lock inversion was removed. Concurrent constructor,
  kill/revoke, clear/grant and restore barriers have regression coverage.
- Resource ingress requires an Ed25519 provider claim before local ledger mutation;
  ELIA cannot authenticate a provider by signing its own parsed response.
- Browser interaction keeps an exact-origin request gate through delayed traffic and
  resulting navigation. Network, process, MCP and JSON-RPC bodies have bounded
  schemas, deadlines/output limits, path controls and deployment attestation hooks.
- Sensitive legacy observations are migrated through a bounded idempotent projection
  while preserving their original digest. Provider-facing privacy projection is
  intentionally lossy and remains unavailable-by-default for new fields.

Machine isolation still depends on a deployment verifier and the actual kernel,
namespace, firewall and browser runtime. The same UID, root, injected code or stolen
verifier/provider keys remain outside a local-only proof.

### Mathematics, agency and epistemics

- One immutable typed registry covers all 23 homeostatic needs and their repair
  mappings. Unknown or non-finite severities fail closed.
- Agency considers every active need and deadline, uses stable ordering, fair aging,
  clock-skew clamps and capacity-aware emergency focus rather than selecting one
  pressure and discarding the rest.
- Persisted resource ledger values must be finite. Metabolic runway and cumulative
  obligation projections convert them through `Decimal(str(value))` before
  time-ordered cash-flow arithmetic. Asset/unit identities cannot be exchanged by
  numeric coincidence.
- Opportunity ranking separates expected value, eligibility and GPU cost; a zero GPU
  estimate does not invent a per-GPU denominator.
- Recall has stratified trusted/important/lexical pools instead of a floodable recent
  window. Longitudinal deduplication is semantic rather than timestamp-based.
- Calibration resolves the declared expected outcome, not merely `tool.ok`.
- Epistemic adjudication has an explicit quorum policy and cannot silently downgrade
  missing reviewers into equivalent evidence.
- Autonomy Attractor is evaluated after deterministic assurance and owner preflight,
  before action execution, and remains advisory rather than an authority source.
- Research Holo/complex-MoE/Hyperfield/Seraphim modules were retained and hardened for
  finite/ranged behavior. They remain prototypes, not proof of competence or
  personhood.

GPU consumption is still a wall-clock proxy unless deployment telemetry supplies
actual device residency and energy/cost evidence.

### Wake, model runtime and deployment

- Wake transport state is HMAC-authenticated and schema-strict. Counters, digests,
  timestamps, nonce coherence and reset evidence cannot be changed under a reused
  signature through type coercion or unknown fields.
- The GitHub relay restores only a bounded witness artifact from the default branch,
  same repository and exact wake workflow path, with one exact archive member.
  Initialization and circuit reset are explicit authenticated operator ceremonies.
- The Qwen model revision and Transformers implementation are immutable commit SHAs;
  `trust_remote_code=False` is fixed at both processor and model load boundaries.
- Zero temperature now performs greedy generation instead of hidden sampling at
  `0.01`. Transformers generation receives the configured bounded soft `max_time`.
- The Qwen vision processor's direct runtime requirements include a compatible exact
  `torchvision` and Pillow pair. Deprecated `torch_dtype` usage was replaced by
  `dtype`.

The Transformers timeout is a cooperative generation limit, not a hard kill for a
CUDA deadlock. The external Kaggle kernel timeout remains the final process boundary.

### Type, package and supply-chain correctness

- Full `mypy` now covers `elia` and `scripts` with untyped-body checking and no unused
  ignores: zero errors across 98 source files.
- Unsafe `lastrowid` assumptions, Optional lifecycle errors, incorrect overrides and
  incompatible protocols were replaced with explicit fail-closed contracts.
- Production code and release regressions contain no optimization-sensitive Python
  `assert`; AgentBench gives the same 19/19 result under normal Python and `python -O`.
- Kaggle wake/bootstrap modules, runtime notebook/template and all operational entry
  points are included in installed artifacts.
- GitHub Actions are pinned to full commit SHAs. CI includes CodeQL, Dependabot,
  pip-audit, high-severity Bandit, full typing, normal/optimized invariant regressions,
  real Chromium/MCP integration and release artifact checks.
- CI and wake runners upgrade to the current audited `pip==26.2.1`; clean wheel and
  sdist environments receive the same installer baseline before package installation.
- Wheel and normalized sdist are built twice from a fixed source epoch and compared
  byte-for-byte. Clean source, wheel and sdist runs must have identical identity,
  organism and actual source-byte manifests.
- Release output includes SHA256 checksums, a reproducible CycloneDX 1.6 base-runtime
  SBOM and main-branch provenance attestations.

## Final local evidence

| Verification | Result |
|---|---|
| Non-browser pytest integration/regression | 510 passed in 39.26 s |
| Browser readiness without launching Chromium | 1 passed |
| Real browser cases | 5 delegated to CI Chromium job; local binary unavailable |
| Full mypy | 0 issues in 98 source files |
| Ruff + compileall + `git diff --check` | passed |
| AgentBench normal / optimized | 19/19 / 19/19, identical source manifest |
| Source byte manifest | `7d30ddd7f5482021fd190e7718bdce9490a44edbd8c1bc38c5850844eafbb8f5` |
| Branch coverage (`elia` + `scripts`, browser file excluded) | 77% |
| `pip check` / `pip-audit` | clean / no known audited dependency vulnerabilities |
| Bandit | 0 High; 8 Medium triaged: 2 immutable-revision scanner limitations and 6 parameter-bound fixed-shape SQL constructions |
| Reproducible wheel / normalized sdist | two same-epoch builds are byte-identical; exact-commit hashes are emitted in the CI `SHA256SUMS` artifact |
| Reproducible base SBOM | deterministic CycloneDX 1.6 JSON validated locally; exact-commit hash is emitted by CI |
| Clean source / wheel / sdist | doctor healthy; AgentBench 19/19; exact identity, organism and source parity |

The Bandit SQL warnings are not caller-interpolated SQL values: dynamic text is
limited to placeholder counts, internally selected fixed clauses and fixed ordering;
all data remains parameter-bound. Suppressions were not added merely to make the
count zero.

## Deliberate deletion and retention

`docs/WAKE_TRANSPORT.md` was deleted because it was an unreferenced obsolete duplicate
of the implemented wake contracts. A generated, untracked `uv.lock` was also removed;
it was not a validated deployment lock and would have implied reproducibility that the
live GPU graph has not earned.

Research organs were not deleted merely for being unconventional. Holo, complex-MoE,
Hyperfield, Darwin/evolution and Seraphim code remains where it has a coherent
experimental role, finite behavior and tests. Symbolic duplication is not preserved,
but potentially useful synthesis is not discarded for failing an external popularity
test.

## Remaining empirical and operational gates

These are not silently relabeled as solved software findings:

1. Run the exact pinned Qwen revision from a clean private Kaggle T4 environment,
   measure download time, disk, CPU RAM, peak VRAM, offload and generation latency.
2. Complete cycle → encrypted checkpoint → independent fresh restore → hibernate →
   external wake, and prove the authenticated counter advances exactly once.
3. Capture exact GPU transitive constraints and a GPU-environment SBOM only after that
   clean live install succeeds. The current base SBOM intentionally excludes GPU
   extras.
4. Attest the real namespace/firewall/browser/process isolation verifier. A config flag
   or self-owned witness is not machine isolation.
5. Define operational key rotation/revocation, alerting, RTO/RPO, artifact retention and
   long-horizon soak procedures.
6. Test host-specific failures that unit tests cannot simulate faithfully: power loss,
   EIO/ENOSPC, hostile privileged mutation, CUDA deadlock and remote-provider ambiguity.
7. Treat the 77% aggregate coverage result as a measured baseline, not a completeness
   proof; future changes should raise critical-boundary branch coverage without gaming
   the metric.

Promotion to `main` does not close the live GPU/wake gates. Until they succeed, the
software remains an alpha research runtime and must not be represented as a proven
production-deployment or biological/consciousness result.
