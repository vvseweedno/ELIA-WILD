# Genesis 1.5 — External Work Ports

Genesis 1.5 connects a locally staged resource-work artifact to a real, explicitly configured external submission channel while preserving the separation between submission, acceptance and verified payment.

## Why this organ exists

Genesis 1.4 can reach:

```text
verified bottleneck
→ exact resource opportunity
→ work plan
→ staged local artifact
```

but a staged file is not external action. Genesis 1.5 adds the next evidence-bearing transitions:

```text
staged
→ configured work port
→ observed submission_ref
→ submitted
→ observed remote outcome
→ pending | accepted | rejected
```

`accepted` is still **not** `realized`. Verified resource realization remains a separate receipt/verifier boundary.

## Authority model

A work port is infrastructure configuration, not model-created authority.

```yaml
tools:
  work_ports:
    enabled: true
    ports:
      marketplace:
        server: configured_mcp_server
        submit_tool: submit_candidate
        outcome_tool: candidate_status
```

The model may choose only:

```json
{"name":"submit_work","args":{"port":"marketplace","work_item_id":42}}
```

It cannot select or override the MCP server, remote tool, transport, credentials or account by adding extra arguments. `WorkPortRegistry` resolves those from the fixed port binding.

Generic MCP authority and work-port authority are intentionally different. A discovered generic MCP tool does not become a submission port merely because it could technically send data.

## Submission contract

`submit_work` requires:

1. work item exists;
2. Resource Ecology status is exactly `staged`;
3. staged artifact path remains inside the private workspace jail;
4. artifact exists and is within the configured bounded size;
5. Genesis 1.5 currently requires UTF-8 text artifacts;
6. chosen port is already configured/enabled;
7. underlying MCP server/tool are already allow-listed;
8. remote result is machine-readable JSON object;
9. remote result contains a non-empty `submission_ref`.

The MCP SDK may provide a machine-readable mapping either in `structured_content` or as a JSON-object text content block. `MCPBody` normalizes these two forms. Arbitrary prose, malformed JSON, arrays and scalar JSON are never promoted to structured evidence.

Only after the remote result is recorded as an Observation does Resource Ecology advance `staged → submitted`.

## Outcome contract

`check_work_outcome` is valid only after a recorded submission. The runtime keeps the remote `submission_ref` locally and supplies it to the preconfigured outcome tool. The model does not need to know or repeat that reference.

The remote outcome vocabulary is deliberately closed:

- `pending` — no terminal work transition;
- `accepted` — external evidence required; work becomes accepted;
- `rejected` — external evidence required; work becomes rejected.

Unknown statuses fail closed.

## Acceptance is not payment

Nothing in `WorkPortRegistry`, `submit_work` or `check_work_outcome` can:

- issue a `VerificationReceipt`;
- mark a resource event verified;
- increase a verified balance;
- change metabolic runway;
- mark accepted work `realized`.

Genesis 1.4's realization rule still applies: accepted work needs a **separately verified positive resource event** with the exact opportunity `(asset, unit)`.

This keeps the causal chain explicit:

```text
submitted artifact
≠ accepted work
≠ payment claim
≠ verified receipt
≠ changed runway
```

## Evidence and privacy

Each submission/outcome attempt has:

- State Bus transaction;
- hashed arguments fingerprint;
- private Observation of the normalized MCP response;
- causal intervention record;
- capability-health event;
- local WorkPortStore row.

The local store may retain `submission_ref` and response fingerprint because the adapter needs them for future polling/audit.

Model-facing, CLI-public and ELIA MCP introspection projections deliberately omit:

- `submission_ref`;
- raw external response body;
- response fingerprint where not required;
- transport credentials.

They retain work-item ID, port name, lifecycle status and Observation IDs.

## Introspection is not execution

ELIA's own MCP server remains read-oriented. It may expose `elia_work_ports` / `elia://work-ports` status, but it does **not** expose `submit_work` or `check_work_outcome` as public MCP tools.

Execution remains inside the organism's configured capability boundary.

## Failure semantics

- unknown/disabled port: fail closed;
- missing/oversize/out-of-jail artifact: fail closed;
- MCP error: failed Observation + capability-health event;
- no machine-readable object: fail closed;
- no `submission_ref`: fail closed;
- unsupported outcome: fail closed;
- repeated failures: normal capability degradation/suppression rules apply.

A resource shortage never broadens this authority.

## Default deployment

All work ports are disabled in `config/genesis.yaml`:

```yaml
work_ports:
  enabled: false
  ports: {}
```

A real deployment must explicitly configure both the work port and its underlying MCP server/tool allow-list. Credentials enter through the existing MCP environment-backed credential boundary; they are not stored in the model context or repository.

## What 1.5 proves

The integration suite uses a real in-process MCP v2 server to prove:

```text
staged
→ submit_work
→ real MCP call
→ submission_ref Observation
→ submitted
→ pending
→ accepted
```

and verifies that `cash/USD` balance remains zero after acceptance.

It does not prove access to any real marketplace, customer account or payment provider. Those are explicit external integrations.

## Next boundary

Genesis 1.6 should add **verified resource ingress adapters**, not a generic payment-writing tool:

```text
accepted work
→ configured verifier adapter reads provider/account evidence
→ exact signed VerificationReceipt
→ verified resource_event
→ Resource Ecology realization linkage
→ measured metabolism/runway change
```

The verifier must remain independent from the LLM and from the submission adapter's claim that work was accepted.
