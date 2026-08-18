# ELIA WILD Whole-Organism Audit — 2026-08-18

**Audited repository:** `vvseweedno/ELIA-WILD`  
**Audited main head:** `68d7d0a5357f1d5092fc24887fcc04cbce0fce87`  
**Audited package version:** `1.6.0a1`  
**Audit branch:** `audit/whole-organism-2026-08-18`  
**Audit type:** logical, architectural, technical, integrity, authority, persistence, security, deployment, reproducibility, scientific-evidence and long-horizon viability review.

## 0. Executive verdict

ELIA WILD is no longer a toy agent scaffold. The repository has a coherent research architecture around a replaceable cognitive substrate, explicit identity artifacts, lineage, Chronicle/CRC, persistent state, bounded tools, deterministic assurance, resource accounting, homeostasis, executive gating, a sensorium/world model/causal-memory layer, external-work abstractions and a differentiated epistemic ecosystem.

The strongest design decision is also the central scientific bet: **the LLM is not treated as the identity**. Identity, continuity, authority and durable state live outside the model call. That is a materially different architecture from a conventional tool-using chat agent.

However, the current implementation has reached a point where breadth has outrun the truth kernel beneath it. Before ELIA WILD is allowed to perform real money-bearing or irreversible external work, two P0 blockers must be closed. Before continuity claims can be treated as strong engineering evidence, several P1 invariants must be strengthened.

**Current promotion verdict:**

- **Research runtime / local bounded organism:** viable alpha.
- **Real external work with irreversible side effects:** **BLOCKED** pending idempotency/outbox work.
- **Real verified financial accounting:** **BLOCKED** pending receipt anti-replay.
- **Strong claim of Chronicle ancestry continuity:** **NOT YET PROVEN** by current CRC comparison.
- **Long-horizon autonomous-survival claim:** **NOT YET EMPIRICALLY ESTABLISHED**.
- **Consciousness / AGI / self-financing-life claims:** correctly not claimed by the project.

The audit therefore does **not** conclude that the architecture is wrong. It concludes that the next generation should stop adding organs temporarily and harden the **continuity + transaction + authority kernel**.

---

## 1. Audit methodology

A subsystem is not counted as a viable organ merely because a source file exists or imports. The intended viability chain is:

```text
declared
→ reachable from production runtime
→ invoked under the intended authority boundary
→ changes only the state it is allowed to change
→ persists atomically or has explicit recovery semantics
→ survives restart/restore
→ detects corruption/replay/rollback
→ has adversarial/fault-injection tests
→ is exercised by a reproducible installed artifact
→ has empirical evidence for claims made about it
```

### Evidence grades

- **C — code-confirmed:** directly established by source inspection.
- **T — test-confirmed design intent:** repository contains a relevant regression/integration test; this audit did not treat the mere presence of a test as proof that current HEAD CI is green.
- **R — runtime evidence required:** cannot be established by static inspection alone.
- **S — scientific evidence required:** needs controlled longitudinal/ablation evidence, not just software correctness.

### Severity

- **P0 — external-safety / accounting blocker:** must be fixed before enabling the affected real external capability.
- **P1 — structural invariant defect:** can invalidate continuity, causality, authority or durable state semantics.
- **P2 — reliability/security/reproducibility weakness:** important before production/long-horizon experiments.
- **P3 — quality/DX/documentation debt:** does not currently invalidate the organism but lowers maintainability or external usability.

---

## 2. What is already strong

### 2.1 Replaceable-brain architecture

The production stack preserves the distinction between identity and model substrate. Later generations layer metabolism, Executive control, resource ecology and epistemic diversity around the same organism rather than silently redefining identity around a new model.

**Status:** GREEN / C.

### 2.2 Deterministic pre-action assurance

`CriticAssurance` checks declared/enabled capabilities, selected skills, identity fingerprint consistency and critical continuity/identity needs independently of hidden model reasoning. This is the correct direction: authority is structural, not rhetorical.

**Status:** GREEN / C.

### 2.3 Economy truth separation

The design explicitly separates:

```text
estimated opportunity value
!= staged work
!= submission
!= acceptance
!= payment claim
!= verified resource
```

Model-authored estimates cannot directly mint verified balance. Verified resource events require a signed verification receipt over exact claim and evidence. This is much stronger than ordinary agent ledgers.

**Status:** GREEN design, RED anti-replay detail / C+T.

### 2.4 Conservative process execution

The process body uses executable allow-lists, absolute executable resolution, `shell=False`, controlled CWD, minimal environment/secret scoping, timeouts and a production-sandbox requirement unless an explicit unsafe development escape is enabled.

**Status:** GREEN / C+T.

### 2.5 Public HTTP SSRF boundary

The raw HTTP body resolves and validates public addresses, connects to the prevalidated target, rechecks peer addressing, bounds methods/output and avoids transparent redirects. This is one of the better-implemented external boundaries in the repository.

**Status:** GREEN / C+T.

### 2.6 Epistemic-data boundary

Epistemic organs and adjudicator prompts explicitly treat questions, web text, biographies and packet contents as untrusted data rather than instructions. Provider-context projection removes or reduces raw sensor payloads, private resource evidence, remote work references and private biography material.

**Status:** GREEN / C+T.

### 2.7 Causal-memory epistemic restraint

`CausalMemoryStore` records intervention/outcome histories but explicitly labels them empirical intervention statistics rather than causal proof, and excludes noop/local introspection from strategic success statistics.

**Status:** GREEN / C.

### 2.8 Fail-closed lifecycle

Lifecycle preflight halts on Chronicle/identity/branch mismatch; force-wake bypasses timing only, not integrity or budget. `ResidentSupervisor` checks vital signs before launching cognition.

**Status:** GREEN / C+T.

### 2.9 Scientific wording

README correctly states that the runtime is not proof of consciousness, AGI, epistemic superiority, economic self-sufficiency or indefinite survival. Pearson-12 diversity is explicitly separated from accuracy, and external survival remains an empirical experiment.

**Status:** GREEN.

---

# 3. Critical findings

## WO-001 — P0 — VerificationReceipt replay can double-count verified resources

**Invariant:** one externally verified economic event must be consumable exactly once for the relevant accounting transition.

**Confirmed:** `VerificationRegistry.verify()` authenticates authority, claim digest, evidence digest, nonce and HMAC signature, but does not persist a consumed-receipt/nonce state. `EconomyStore.record_resource_event()` inserts a new verified row whenever the same valid receipt is presented with the same exact claim/evidence. No unique constraint currently makes that receipt single-use.

**Failure mode:** one genuine payment receipt can be replayed N times and the verified balance increases N times.

**Impact:** verified runway, resource pressure, opportunity selection and self-financing metrics can become false while every individual signature remains cryptographically valid.

**Scope:** the same trust primitive is also used by verified obligations/mutations and body-revision evaluation; anti-replay should be a common verification-kernel property rather than an Economy-only patch.

**Acceptance criteria:**

1. Stable receipt identity (`authority + nonce` or a canonical receipt digest) is persisted.
2. Consumption is unique and atomic with the domain transition it authorizes.
3. Replaying the exact receipt is deterministic no-op or hard rejection and cannot create a second domain mutation.
4. Concurrent replay race is tested.
5. Restore/restart preserves consumed receipt state.
6. Tests cover economy, metabolism and revision receipts.

**Promotion gate:** do not enable real payment/resource adapters before closure.

**Evidence:** C+T.

---

## WO-002 — P0 before external-work enablement — ambiguous remote success can cause duplicate submission

**Invariant:** an irreversible external action must have exactly-once semantic intent, even when the local process dies after the remote service acts but before local persistence completes.

**Confirmed:** WorkPort submission performs the remote MCP side effect and only afterwards records the local observation/submission/resource/state-bus transition. If the remote submit succeeds and the process crashes before durable local commit, a retry can submit the same work again because there is no required idempotency key/outbox/remote dedupe contract.

**Failure mode:**

```text
local intent
→ remote submit succeeds
→ process dies
→ local state still says not submitted
→ retry
→ duplicate external submission
```

`reconcile_incomplete()` can mark a local transaction aborted, but it cannot know whether the remote world already changed.

**Acceptance criteria:**

1. Persist a durable action intent before the first remote side effect.
2. Give every side-effecting action a stable idempotency key derived from immutable local intent identity.
3. Require WorkPort adapters to support idempotency or a deterministic remote lookup/reconciliation protocol.
4. Add explicit `indeterminate` state; never automatically retry ambiguous external success.
5. Crash-injection tests at every boundary: before send, after send/before response, after response/before local commit, after local commit.
6. Recovery reconciles remote state before any retry.

**Promotion gate:** current default `work_ports.enabled=false` is correct; keep it disabled for real irreversible work until closure.

**Evidence:** C+T.

---

# 4. P1 structural findings

## WO-003 — P1 — CRC continuity does not prove Chronicle prefix ancestry

**Invariant:** a new history is continuous with an old history only if the old accepted Chronicle head exists as an exact prefix/ancestor of the new Chronicle.

**Confirmed:** current CRC comparison checks right-side Chronicle validity and non-decreasing sequence, but the old `chronicle_hash` is not used to establish that the old head occurs at the old sequence in the new chain.

**Failure mode:** a completely rewritten and internally rehashed Chronicle with the same identity/branch and same-or-higher sequence can satisfy the continuity comparison.

**Acceptance criteria:**

- Add `Chronicle.hash_at_seq(seq)` / anchor lookup or equivalent Merkle/accepted-head semantics.
- A continuation must prove `current_hash_at(previous.seq) == previous.chronicle_hash`.
- Explicit fork is the only allowed branch ancestry discontinuity and must carry a parent accepted-head reference.
- Regression tests replace history while preserving length and show `broken`.

**Evidence:** C+T.

---

## WO-004 — P1 — OrganismStateBus abort does not roll back organism state

**Invariant:** an aborted organism transition must not be presented as if its state mutations never happened.

**Confirmed:** StateBus has a transaction/event hash chain, but many organism stores commit through independent SQLite connections. During a cognitive cycle, memory/self-model/goals/opportunities and other durable changes can commit before a later exception. The cycle can then be marked `aborted` while those durable changes remain.

**Failure mode:** **an aborted life-transition leaves real durable state.**

This is not necessarily wrong if the architecture is explicitly event-sourced with compensating semantics, but the current naming/claims imply stronger transaction semantics than implementation provides.

**Acceptance criteria:** choose and implement one explicit model:

A. **True Unit of Work:** all local SQLite changes for one accepted transition share one connection/transaction; or

B. **Accepted-head event sourcing:** writes may be prepared independently but are invisible to authoritative projections until one atomic accepted-head commit, with deterministic recovery/compensation.

Then:
- action observations, world/self updates, economy/resource transitions and Chronicle head must derive from accepted transactions only;
- abort tests must prove authoritative projections are unchanged;
- crash tests must cover every commit boundary.

**Evidence:** C+T.

---

## WO-005 — P1 — WorldModel trusted adjudication accepts an authority string, not cryptographic authority

**Invariant:** trusted facts should use the same authority semantics as trusted money/resources: caller text is not authority.

**Confirmed:** the model cannot directly assign `verified`, which is good. However, `WorldModelStore.adjudicate()` accepts a non-empty `authority` string and can set `verified`/`refuted` without a VerificationRegistry receipt. The current unit test explicitly treats `authority="test-verifier"` as sufficient.

**Impact:** the World Model has a weaker trust root than Economy/Metabolism/Evolution. A future or erroneous runtime caller can promote a hypothesis to verified fact merely by choosing an authority label.

**Acceptance criteria:**

- trusted world adjudication requires an authenticated receipt over exact belief claim/status/evidence/observation provenance;
- authority strings become display metadata only;
- receipt replay is covered by WO-001;
- tests prove arbitrary strings cannot promote a belief.

**Evidence:** C+T.

---

## WO-006 — P1 — `organism.healthy` proves anatomy availability, not organism viability/wiring

**Invariant:** a required organ marked healthy should be demonstrated as part of the production organism, not merely importable.

**Confirmed:** `OrganismManifest.audit()` checks artifact existence or Python module/symbol importability and fingerprints implementation files. It does not prove that a required organ is wired into the current production runtime, invoked under correct authority, persists expected state or participates in recovery.

**Failure mode:** a required organ can become dead/decorative code while `elia-vitals` remains healthy.

**Acceptance criteria:** introduce machine-readable **viability contracts** per required organ:

```text
producer / consumer
required runtime path
state owned
read/write authority
health probe
persistence probe
recovery probe
expected event/evidence
```

`elia-vitals --deep` should execute model-independent semantic probes and verify wiring against the current production runtime graph.

**Evidence:** C.

---

## WO-007 — P1 — packaged-install portability is not established

**Invariant:** a substrate-independent organism should boot from its release artifact outside a repository checkout.

**Confirmed:** setuptools discovers `elia*` packages, while canonical `config/`, `skills/` and related organism artifacts live outside the Python package. No package-data mapping is declared. `load_config()` and the CLI default depend on an external `config/genesis.yaml` and project-root-relative artifacts. CI uses editable installs from the checkout.

**Likely failure:** wheel/sdist installation in a clean directory has Python code but not the canonical organism anatomy/config/skills expected by default commands.

**Acceptance criteria:**

1. Decide canonical installation model: package resources, explicit immutable data directory, or generated deployment bundle.
2. Build wheel + sdist in CI.
3. Install each into a clean temp environment outside repository root.
4. Run `elia-doctor`, zero-GPU bootstrap, vitals, status and supervisor dry-run there.
5. Compare artifact fingerprints to checkout baseline.

**Evidence:** C; clean-wheel runtime verification still required.

---

## WO-008 — P1 — MCP URL prevalidation is not connection pinning

**Invariant:** a host validated as public must not be resolved a second time to a private/internal address at connection time.

**Confirmed:** MCP HTTP transport prevalidates URLs with public/private checks, but the actual client transport can resolve the hostname again. In-process MCP tests bypass real DNS/network behavior.

**Impact:** DNS rebinding / TOCTOU SSRF remains possible unless a trusted external network sandbox is guaranteed.

**Acceptance criteria:**

- either use an IP-pinned/peer-validated transport equivalent to the raw HTTP body;
- or require a machine-verified egress sandbox for every remote MCP transport, not only credential-bearing cases;
- integration test with controlled rebinding DNS / private second resolution.

**Evidence:** C+T.

---

## WO-009 — P1 — branch fork is a multi-store, non-atomic identity transition

**Invariant:** branch ancestry must never have a state where lineage, branch metadata, Chronicle and CRC baseline disagree about which branch is active.

**Confirmed:** fork sequentially archives CRC, inserts lineage, updates multiple metadata values, appends Chronicle event and removes active baseline/vitals. A process failure between operations can leave partially transitioned identity state.

**Acceptance criteria:** fork through the same accepted-head/Unit-of-Work protocol as WO-004; parent head must be immutable ancestry evidence; recovery must deterministically finish or reject a prepared fork.

**Evidence:** C.

---

## WO-010 — P1 — online lineage verification truncates ancestry after 1000 events

**Invariant:** lineage integrity must not become weaker merely because the organism lived longer.

**Confirmed:** `IdentityStore.lineage()` clamps requests to 1000 and `verify_lineage()` checks that bounded set. Earlier ancestry eventually falls outside routine verification.

**Acceptance criteria:** hash-chain lineage events or create signed/checkpointed ancestry roots so verification is O(recent window) without dropping old integrity; test >1000 events including tamper before the recent window.

**Evidence:** C.

---

## WO-011 — P1 — resource staging is split across a committed base cycle and post-commit ecology transition

**Invariant:** a successful local action and the resource-ecology state that interprets it should share one accepted transition.

**Confirmed:** Resource runtime completes the base cycle, then separately converts a staged deliverable into resource-ecology state and appends a Chronicle event. A failure in the post-step can leave action success without matching ecology transition.

**Acceptance criteria:** include the ecology transition in the accepted action Unit-of-Work/outbox projection, make it idempotent, and test crash after action/before ecology commit.

**Evidence:** C.

---

# 5. P2 integrity / reliability / security findings

## WO-012 — self-model payload fingerprint is not recomputed on read

`latest_self_model()` returns stored JSON and stored fingerprint but does not recompute the payload digest and reject mismatch. Checkpoint authentication protects restored snapshots, but live online tamper/corruption between checkpoints has weaker detection.

**Fix:** verify snapshot fingerprint on every authoritative read or hash-chain/signed-head self-model snapshots.

---

## WO-013 — observation payload digest is preserved but not verified on normal read

Sensorium stores original payload digest and preserves it through compaction, but normal `get()` does not validate stored payload against the expected digest/compaction marker.

**Fix:** add verified-read path; authoritative world-model ingestion should consume only verified observation envelopes.

---

## WO-014 — Chronicle is tamper-evident, not independently authenticated against a hostile host

The SHA chain catches malformed/partial/local corruption, but an actor able to rewrite the complete Chronicle can recompute the chain. Checkpoint HMAC provides a stronger authenticated snapshot boundary, but per-event/history anchoring is still a trusted-host assumption.

**Fix:** document threat model explicitly. For distributed/untrusted body experiments, sign accepted heads or externally anchor periodic roots with a key unavailable to the mutable cognitive body.

---

## WO-015 — checkpoint capture/commit still has crash/quiescence windows

Checkpoint implementation is otherwise strong: authenticated manifest, file digests, SQLite backup/integrity, logical state digest, symlink/path checks, Chronicle head checks, staged restore and rollback anchors.

Remaining hardening targets:

- observational before/after quiescence is not a global write barrier;
- a crash after replacing the archive but before counter/anchor metadata update can leave an orphaned checkpoint state;
- local rollback anchor trust is still tied to trusted-host assumptions.

**Fix:** checkpoint through a global accepted-head barrier and atomic generation index/manifest commit.

---

## WO-016 — browser trusted-origin interaction does not fully constrain the resulting destination

Browser interaction correctly requires explicit enablement, trusted interaction origins and a network-isolation attestation. But an interaction on a trusted page can still cause navigation/form submission to another destination unless destination effects are independently constrained.

**Fix:** route/network interception policy for side-effecting browser requests; origin + method + destination policy; block metadata/private ranges at the network layer; log redirect/form target before commit where possible.

---

## WO-017 — `network_isolation_confirmed` is a manual assertion, not an attested deployment property

The example systemd unit is meaningfully hardened at the process/filesystem/kernel level, but it does not itself prove egress denial to loopback, RFC1918/link-local/cloud metadata destinations. Therefore the boolean must not be inferred from use of the example service.

**Fix:** add `elia-doctor --network-sandbox` probes and an attestation artifact generated by deployment tooling. High-risk network adapters should require the attestation, not a free-form config boolean.

---

## WO-018 — no repository LICENSE

The audited public root did not contain `LICENSE`. For external research adoption this leaves reuse rights ambiguous.

**Fix:** choose and add an explicit license consistent with project goals and dependency obligations.

---

## WO-019 — no SECURITY.md / explicit threat model

The audited root did not contain `SECURITY.md`.

The project now needs a formal threat matrix at least for:

- trusted host vs hostile host;
- trusted verifier keys;
- malicious web/MCP content;
- malicious/compromised model provider;
- state corruption vs deliberate state rewrite;
- DNS/network adversary;
- replay/rollback;
- crash at external-effect boundary;
- secret scope;
- compromised deployment operator.

---

## WO-020 — CI breadth is good; artifact/fault-injection depth is insufficient

The repository has broad tests across identity, checkpointing, lifecycle, StateBus, body adapters, redaction/privacy, metabolism/resource ecology, work ports and epistemic layers. That breadth is a real strength.

The missing class is **destructive testing of the organism's invariants**:

- power loss after every state boundary;
- remote-success/local-crash;
- receipt replay/concurrent replay;
- Chronicle prefix replacement;
- lineage tamper outside recent window;
- concurrent writers;
- SQLite busy/IO/disk-full conditions;
- partial checkpoint/counter update;
- DNS rebinding;
- corrupted stored payloads;
- clean wheel/sdist install;
- mutation/property tests for parsers and state machines.

**Fix:** add a dedicated `continuity-chaos` CI lane and a release-artifact lane.

---

# 6. Scientific audit

## WO-021 — S — long-horizon organism continuity remains an experiment, not a demonstrated result

The repository contains longitudinal continuity instrumentation, supervisor/wake/checkpoint mechanisms and explicit falsification language. That is the right foundation.

What is not yet established by repository code alone:

```text
72 h → 7 d → 30 d → 90 d
```

with repeated:

```text
wake → cognition → bounded action → outcome → learning
→ checkpoint → process death → restore
→ machine/model migration → continuity test
```

and low/no human task injection.

**Required evidence:** append-only experiment manifest, environment hashes, accepted-head roots, intervention count, human-intervention minutes, model swaps, crash/recovery events, continuity score trajectory, resource truth and all falsification events.

---

## WO-022 — S — Pearson-12 / epistemic ecosystem is implemented as a hypothesis, not proven superior cognition

The software carefully avoids equating diversity with accuracy. Keep that discipline.

Promotion from interesting architecture to demonstrated cognitive gain requires equal-budget external-ground-truth ablations:

```text
Pearson-12
vs homogeneous reviewers
vs random attention roles
vs domain-specialized reviewers
```

Measure accuracy, calibration, correlated-error rate, contradiction discovery, falsifier quality, latency and token/compute cost. Predeclare acceptance/rejection thresholds.

---

# 7. Architectural scorecard

These are audit statuses, not claims of universal scientific quality.

| Area | Status | Why |
|---|---|---|
| Identity/model separation | GREEN | Explicit non-model identity substrate and lineage concept |
| Default external authority | GREEN/AMBER | Strong defaults; several trust APIs still inconsistent |
| Economy truth model | AMBER/RED | Concept strong; receipt replay is P0 |
| External side-effect semantics | RED | no durable idempotent outbox for ambiguous remote success |
| Chronicle/CRC continuity | AMBER/RED | valid-chain checks strong; prefix ancestry not proven |
| State transaction semantics | RED | StateBus does not make organism writes atomic |
| Checkpoint/restore | GREEN/AMBER | unusually strong, with remaining crash/quiescence hardening |
| Process body | GREEN | conservative capability design |
| Raw HTTP body | GREEN | strong SSRF/peer validation |
| MCP/browser networking | AMBER | TOCTOU/network-attestation gaps |
| Privacy/provider projection | GREEN | explicit redaction/projection layer |
| Vitals | AMBER | strong anatomy gate, insufficient semantic wiring proof |
| Packaging portability | AMBER/RED | editable checkout is tested; clean release artifact is not |
| Test breadth | GREEN | broad module/integration coverage |
| Fault-injection depth | RED/AMBER | central crash/replay cases missing |
| Scientific falsifiability | GREEN | claims are generally scoped and falsifiable |
| Long-horizon empirical proof | RED / not yet run | this is the next experiment, not a software defect |

---

# 8. Required remediation order

## Gate 0 — freeze real external/money authority

Until these are complete, keep real WorkPorts/payment adapters disabled:

1. **Central anti-replay verification ledger** — WO-001.
2. **Durable external-action intent/outbox + idempotency + indeterminate reconciliation** — WO-002.
3. Crash-injection tests for both.

## Gate 1 — continuity truth kernel

4. Chronicle prefix/ancestor proof in CRC — WO-003.
5. Replace pseudo-transactional StateBus semantics with a real Unit-of-Work or accepted-head event-sourced commit — WO-004.
6. Move branch fork and resource staging onto that transition protocol — WO-009/WO-011.
7. Make lineage integrity unbounded through hash roots/checkpoints rather than a 1000-event tail — WO-010.

## Gate 2 — one authority model

8. Make WorldModel trusted adjudication use the same cryptographic verification kernel — WO-005.
9. Verify self-model/observation payload digests on authoritative reads — WO-012/013.
10. Define hostile-host vs trusted-host threat model and signed accepted-head strategy — WO-014/019.

## Gate 3 — network + deployment truth

11. Pin MCP connections or require attested egress isolation — WO-008.
12. Restrict browser side-effect destinations — WO-016.
13. Replace manual network-isolation boolean with machine-verifiable deployment attestation — WO-017.

## Gate 4 — reproducible organism artifact

14. Package/deploy canonical config, skills and anatomy — WO-007.
15. Clean wheel/sdist install tests outside checkout.
16. Add LICENSE + SECURITY.md.
17. Add continuity-chaos/fault-injection CI.

## Gate 5 — science

18. Real external wake/restore cycle.
19. Model/substrate swap experiments.
20. 72h → 7d → 30d → 90d longitudinal campaign.
21. Equal-budget Pearson-12 ablation with external ground truth.

---

# 9. Proposed next architecture: Continuity Kernel 2.0

The audit suggests one architectural consolidation instead of another feature layer.

```text
                       TRUST ROOT
          verifier keys / accepted-head key
                            │
                            ▼
┌─────────────────────────────────────────────────┐
│             CONTINUITY KERNEL 2.0               │
│                                                 │
│  AcceptedHead                                   │
│  ├─ identity / branch / parent head             │
│  ├─ Chronicle anchor                            │
│  ├─ state projection root                       │
│  ├─ consumed verification receipts root         │
│  └─ external action intent/outcome root          │
│                                                 │
│  PreparedTransition                             │
│      ↓ deterministic validators                 │
│  Commit | Reject | Quarantine | Indeterminate   │
└─────────────────────────────────────────────────┘
             │                         │
             ▼                         ▼
      local projections          external outbox
 memory/world/economy/...   idempotent action/reconcile
```

The StateBus then becomes a real nervous system: not merely a journal saying what happened, but the place that decides which transitions became part of the organism's accepted trajectory.

This also makes the scientific thesis sharper:

> ELIA's identity is not every mutable byte in SQLite. It is the authorized, causally ordered lineage of accepted states and actions that preserve the Subject Core invariants across replaceable substrates.

That statement is testable.

---

# 10. Definition of “Whole-Organism Healthy” after remediation

A future `elia-vitals --deep` should not return healthy until all of the following are true:

```text
identity bundle valid
AND lineage ancestry valid
AND Chronicle previous accepted head is an ancestor
AND current accepted state root verifies
AND no unresolved replay / rollback
AND no stale prepared transition requiring recovery
AND no ambiguous external side effect hidden as aborted
AND required organs are wired to production runtime
AND authority graph matches manifest
AND trusted facts/resources have authenticated provenance
AND release artifact can boot from a clean install
AND latest checkpoint can restore and continue the same branch
```

Only then does `healthy` mean organism viability rather than module availability.

---

# 11. Final audit conclusion

The project has crossed an important boundary. Its problem is no longer that it is “just prompts around an LLM.” The opposite problem has appeared: the organism has enough real organs that **coordination truth** is now harder than adding capability.

The next breakthrough should therefore be deliberately unglamorous and foundational:

```text
anti-replay
+ accepted-head ancestry
+ crash-safe transitions
+ idempotent external action
+ one cryptographic authority model
+ semantic vitals
```

Once those are real, World Model, Causal Memory, metabolism, Executive control, epistemic organs, work ecology and future self-evolution stop being adjacent subsystems and become one causally continuous organism.

**Audit verdict:** preserve the current feature surface; harden the kernel before expanding it.

---

## Audit limitations

This document is a static/structural audit of the audited source tree, tests, configuration and workflow definitions. It does not claim to have executed the current repository on a local machine or independently attested the latest GitHub Actions result during this audit. Findings labelled R/S explicitly require runtime or scientific experiments. A later verification pass should bind this report to concrete CI run IDs, release-artifact hashes and long-horizon experiment evidence.
