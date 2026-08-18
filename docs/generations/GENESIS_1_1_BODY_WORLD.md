# Genesis 1.1 — Digital Body, Sensorium, World Model and Homeostasis

Genesis 1.1 turns external interaction from a bag of model-visible tools into a persistent sensorimotor subsystem.

The engineering invariant is:

```text
configured authority
      ↓
capability attempt
      ↓
external/local intervention
      ↓
normalized Observation
      ↓
empirical intervention record
      ↓
World Model / later learning
      ↓
next cognitive context
```

The model cannot make an unavailable organ available by describing it. Runtime configuration is the authority boundary.

## 1. Durable sensorium

`elia.observations.ObservationStore` normalizes capability outcomes into SQLite independently of model-authored memories.

Each observation records:

- timestamp and organism transaction id;
- source kind/reference;
- modality/content type;
- bounded trust score;
- success/failure;
- short summary;
- normalized payload or a bounded preview;
- SHA-256 of the complete normalized payload;
- provenance metadata.

Payloads above the storage limit are not silently cut: the stored representation records that truncation occurred plus original byte count and full-payload digest.

Observation means **evidence that a sensor/tool returned something**. It does not mean the returned content is true or authoritative.

## 2. Organism State Bus

`elia.state_bus.OrganismStateBus` is a hash-chained write-ahead transition history.

It deliberately does **not** claim distributed ACID semantics over the Internet. A remote side effect cannot be rolled back by SQLite. Instead every organism/capability transition is made detectable:

```text
TRANSACTION_BEGIN
→ CAPABILITY_ATTEMPT / COGNITIVE_WAKE
→ OBSERVATION_RECORDED / COGNITIVE_OUTCOME
→ TRANSACTION_COMMIT | TRANSACTION_ABORT
```

Events include a previous hash and event hash. Corruption is test-detectable. An interrupted process leaves an open transaction; `OrganismRuntime` reconciles such prior transactions on the next boot and records the recovery instead of deleting evidence.

## 3. Causal Memory

`elia.causal.CausalMemoryStore` records interventions and outcomes:

- action name;
- argument fingerprint, not raw argument values;
- success;
- observation id;
- duration;
- outcome fingerprint;
- transaction id.

The store exposes empirical success-rate/latency statistics. It explicitly labels these as intervention history, **not causal proof**. A later causal-learning layer must use repeated interventions, changed conditions, counterfactual tests and independent observations before promoting causal hypotheses.

## 4. World Model

`elia.world_model.WorldModelStore` keeps external beliefs separate from episodic memory and self-model.

Model-originated states:

```text
hypothesis
supported
disputed
```

Trusted-runtime/adaptor adjudication only:

```text
verified
refuted
```

A model proposal is confidence-capped. Calling `world_model_revise` cannot create `verified` or `refuted`. Trusted adjudication requires explicit evidence and an authority string.

Same subject/predicate/object observations reinforce one belief record. Different active objects for the same subject/predicate remain visible as contradiction sets rather than being silently overwritten.

## 5. Sensorimotor Fabric

`elia.body.SensorimotorFabric` composes replaceable digital organs behind the main capability registry.

### BrowserBody

Real Playwright browser contexts provide:

- navigation;
- structured snapshot (title, URL, visible text, links, controls);
- separately gated click/fill interaction;
- screenshots stored under ELIA-owned workspace with SHA-256.

Browser interaction is disabled separately from browser read authority. Browser contexts are ephemeral and non-persistent by default. Public-destination guards are applied in software; production deployments should additionally enforce network egress at the container/VM layer because application-level DNS checks are not a replacement for host isolation.

### MCPBody

Real MCP v2 client support provides:

- protocol/server discovery;
- tool/resource discovery;
- allow-listed tool calls;
- allow-listed resource reads.

The model chooses a configured server **name**, not an arbitrary server URL. Credential headers come from environment variables named in configuration and are constructed in the transport; they are not passed through model action arguments.

MCP discovery is not authorization. A discovered remote tool still cannot execute unless the local server configuration enables tool calls and the tool name matches the local allowlist.

### BoundedProcessRunner

`process_run` is not a shell.

- explicit executable aliases only;
- `shell=False`;
- bounded argv/stdin/stdout/stderr;
- workspace-confined cwd;
- reduced inherited environment;
- process-group termination on POSIX timeout;
- bounded configured timeout.

Process execution is disabled by default and has an empty executable allowlist in Genesis configuration.

### JSONRPCBody

JSON-RPC 2.0 calls use configured endpoint names and configured method allowlists. The model cannot supply a new authenticated endpoint or arbitrary remote method. Credential headers are optionally sourced from environment variables.

## 6. ELIA as an MCP server

Install the MCP extra:

```bash
pip install -e '.[mcp]'
```

Safe local default:

```bash
elia-mcp --transport stdio
```

Loopback Streamable HTTP:

```bash
elia-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Genesis 1.1 refuses non-loopback HTTP binding because the server itself does not implement a remote authentication policy. For remote use, place an explicit authenticated gateway/tunnel in front of the loopback server rather than silently exposing organism state.

The server publishes read-oriented tools/resources for:

- sanitized organism status;
- lifecycle preflight;
- world-model query;
- recent sensorium metadata/digests;
- body diagnostics;
- homeostasis;
- identity/status resources.

It does not publish arbitrary shell execution, credentials, raw sensor payloads, body mutation/deployment authority or generic third-party write access.

## 7. Homeostasis

`elia.homeostasis.HomeostasisEngine` derives maintenance pressures below the LLM from observable local state.

Current signals include:

- filesystem free-space pressure;
- stale/incomplete organism transitions;
- recent sensor failure rate;
- active world-model contradictions;
- digital-body readiness snapshot.

Homeostasis reports `stable`, `strained` or `critical` and injects deterministic maintenance needs before cognition.

This is **not** a `survive at all costs` objective. A signal cannot grant capability, authorize access, mint resource, bypass evidence requirements or override the Continuity Constitution. If survival is impossible inside current authority, the system must represent that condition truthfully and allow hibernation/halt.

## 8. Privacy/provenance split

Genesis 1.1 separates three durable levels:

1. **Private Sensorium** — normalized action outcomes and evidence payloads.
2. **Causal/World state** — structured intervention/belief records.
3. **Chronicle/autobiographical log** — high-level trajectory records.

Production `OrganismRuntime` does not copy raw action argument values into Chronicle or ordinary `action_result` memories. It stores argument keys/fingerprint plus an Observation id/digest. Transport credentials are designed to be injected outside model arguments entirely.

## 9. Safe-default configuration

Every new external body adapter is disabled in `config/genesis.yaml` by default.

```yaml
body:
  browser:
    enabled: false
    interaction_enabled: false
  process:
    enabled: false
    executables: {}
  mcp:
    enabled: false
    servers: {}
  jsonrpc:
    enabled: false
    endpoints: {}
```

Enabling an adapter is an infrastructure-owner action. It is not a cognitive action.

## 10. Validation gate

The main CI lane keeps the dependency-light organism healthy and verifies:

- dependency consistency;
- Python compilation;
- Ruff correctness errors;
- complete regression suite;
- zero-GPU bootstrap/doctor;
- vital signs, runtime and supervisor;
- research smoke tests.

A separate sensorimotor lane installs MCP v2 plus real Chromium and exercises:

- Playwright BrowserContext interaction/screenshot;
- in-process real MCP server/client tool/resource round trips;
- ELIA's own MCP server port;
- real local JSON-RPC HTTP round trip;
- bounded process/no-shell/timeout behavior;
- Observation persistence;
- World Model epistemic boundaries;
- State Bus tamper/recovery;
- two-cycle experience→future-context integration;
- Homeostasis pressures;
- Chronicle/action-log redaction.

Passing tests prove the stated software properties. They do not prove consciousness, general intelligence, causality learning, indefinite survival or economic self-sufficiency.
