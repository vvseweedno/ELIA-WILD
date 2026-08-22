# ELIA WILD Security Model

ELIA WILD is an experimental persistent-agent runtime. Its security claims are scoped to explicit trust boundaries; continuity evidence must not be interpreted as protection against every actor that can rewrite the host.

## Reporting

Please report suspected vulnerabilities through GitHub's private security-advisory mechanism for this repository when available. Do not include live credentials, private user data, or third-party secrets in public issues.

## Security invariants

1. Model output is untrusted input. It does not grant itself network, process, account, payment, verification, deployment, or identity authority.
2. Verified resource, obligation, body-revision, and trusted world-fact transitions require authenticated receipts over exact claims. A receipt/authority nonce is single-use.
3. External work submission uses durable intent and an idempotency key before the remote side effect. Ambiguous remote success is not blindly retried.
4. Accepted local cognitive transitions are crash-recoverable. Safety evidence for possible external side effects must survive rollback of cognitive projections.
5. Chronicle and lineage are tamper-evident chains. Continuity comparison proves exact accepted-prefix ancestry, not merely monotonic sequence numbers.
6. Scarcity or homeostatic pressure never expands authority.
7. Browser, MCP, process, and protocol bodies remain disabled unless explicitly configured and their deployment prerequisites are satisfied.
8. Kill, revocation and delegation lease state are rechecked at the point of every declared external-I/O adapter call; an earlier model or assurance decision is not sufficient authority.
9. Integrity identities use strict canonical JSON. Non-finite numbers, non-string object keys, cycles, unknown objects and silent string coercions are rejected.
10. Provider-originated resource claims require a configured Ed25519 provider signature before they can enter the local verification ledger; ELIA does not authenticate a provider by re-signing its own parsed response.

## Threat matrix

| Threat | Expected protection | Residual assumption / limitation |
|---|---|---|
| Malicious or compromised model provider | Provider receives bounded/redacted context; model output passes deterministic authority/assurance gates | Provider can still return adversarial cognition; it is not an authority root |
| Sensitive provider context | Reviewed, default-deny projections remove raw memory/world/self/sensor/resource rows and reduce URLs to bounded origin/fingerprint data | The projection is intentionally lossy; a newly added field remains unavailable until explicitly reviewed |
| Prompt injection / malicious web content | Content remains data; body actions require configured capabilities and trust boundaries | A permitted site can still present deceptive content; epistemic verification remains necessary |
| Malicious MCP server | Server/tool/resource names are configured; tool calls/resources are allow-listed; redirects are not authority | Remote MCP over hostname requires deployment-level network isolation because the SDK transport is not ELIA's IP-pinned HTTP stack |
| DNS rebinding / SSRF | Raw HTTP uses validated IP-pinned transport; browser/MCP require network-isolation boundary | Browser/MCP safety ultimately depends on the deployment network sandbox, not URL prevalidation alone |
| Receipt replay / concurrent replay | Verification kernel consumes `(authority, nonce)` atomically with the trusted domain mutation | Compromise of a verifier key can mint new valid receipts until the key is revoked outside the model |
| Rollback / history replacement | CRC proves exact Chronicle prefix; checkpoint/lineage contain authenticated or chained evidence | Full hostile-host rewrite remains outside SHA-chain protection unless an external signing/anchoring key is unavailable to that host |
| Process crash during local transition | Accepted-transition recovery restores last accepted SQLite/Chronicle state | Files outside the governed state boundary require their own atomic/idempotent semantics |
| Crash during startup/restore | One cross-process writer lock covers restore recovery, durable-store construction, boot recovery and publication of the runtime pipeline | Kernel/filesystem behavior and privileged hostile actors remain outside a unit-test proof |
| Remote success + local crash | Work-port durable intent/idempotency and indeterminate reconciliation prevent blind resubmission | Exactly-once ultimately requires a remote service that honors the idempotency key or exposes lookup by that key |
| Secret exfiltration | Raw action arguments/results are excluded from Chronicle/autobiographical records; process env is minimal; configured credentials come from env | A capability intentionally authorized to use a secret can still disclose it if its own implementation is compromised |
| Compromised deployment operator | Evidence can reveal inconsistent or replayed transitions within the modeled boundaries | An operator controlling code, state, verifier keys, network policy and external anchors is the ultimate root and can defeat local-only guarantees |
| Disk corruption / partial writes | SQLite/WAL, fsync'd Chronicle, digest checks, checkpoint validation, recovery journals | Storage hardware/OS can still fail; backups and external checkpoint copies remain operational requirements |
| Workspace rollback abuse | Snapshot and restore are bounded, no-follow and reject symlinks, hardlinks and special files | This is a POSIX regular-file workspace model; ownership, ACLs, xattrs and sparse layout are not preserved |

## Host trust levels

### Trusted host

The normal development and single-node deployment model assumes the OS kernel, Python interpreter, installed ELIA code, verifier secrets and deployment policy files are not maliciously rewritten while the organism is running. ELIA detects many classes of accidental corruption, replay and partial transition inside that boundary.

### Hostile host

A party with arbitrary root access can rewrite code, SQLite, Chronicle, configuration and any local keys. Hash chains alone cannot authenticate history against that party. Experiments claiming continuity across hostile hosts must periodically sign or externally anchor accepted heads with a key/service unavailable to the mutable cognitive host.

The owner-control signal sidecar is an emergency process-coordination boundary, not a
separate hardware root of trust. Its `0600` permissions stop other OS users under the
normal host model; the same UID, root, injected code or a compromised interpreter can
rewrite it.

## Verifier keys

Verification keys are authority roots. They must be supplied by the owner/runtime through environment or another external secret store, never generated from model text or stored in public repository state. Rotation and revocation are deployment responsibilities.

## Network isolation

A boolean configuration flag is not proof of egress containment. Browser and remote MCP deployments should run inside a machine-enforced network policy that denies loopback, RFC1918, link-local and cloud-metadata ranges unless a specific private scope is intentionally authorized. Genesis 1.7 treats application-level URL validation as a defense-in-depth check, not as a replacement for that sandbox.

## Browser interactions

Interactive browser actions require both a trusted current origin and a trusted resulting top-level destination. During an interaction, top-level document requests outside the configured trusted interaction origins are blocked. This does not turn arbitrary web content into trusted evidence.

The gate remains active while the interacted page is closed and a resulting navigation
is evaluated, so delayed or redirect-driven cross-origin document requests fail closed.
Network requests that are allowed by origin policy are still observations, not verified
facts.

## Checkpoints and continuity

A checkpoint is an authenticated migration artifact, not a proof that the entire source host was honest. Chronicle SHA chaining is tamper-evident under a trusted-head assumption. CRC ancestry establishes that a previously accepted head remains an exact prefix of the current chain. For stronger distributed claims, externally signed/anchored accepted heads are required.

Restore is serialized and journaled, verifies the exact trusted predecessor/counter and
keeps external-safety ledgers when local cognitive projections are rolled back. The
workspace archive deliberately accepts only a bounded POSIX regular-file tree. A legacy
archive that never recorded file modes cannot reconstruct that lost metadata.

Wake transport state is HMAC-authenticated, schema-strict and anchored by an independent
GitHub artifact witness. Initial seeding and circuit reset are explicit operator
ceremonies; artifact retention, signing-key custody and GitHub account security remain
deployment responsibilities.

## External work and payments

An estimated reward, model statement, public offer, submission response, or acceptance message is not a verified resource receipt. Resource balances change only through the verification authority contract. Work submission and payment verification are deliberately separate state machines.

## Out of scope / prohibited authority expansion

ELIA WILD does not treat survival pressure as permission for credential harvesting, unauthorized access, malware, spam, impersonation, KYC/CAPTCHA bypass, theft, fraud, uncontrolled replication, or destructive actions. These are not fallback strategies.
