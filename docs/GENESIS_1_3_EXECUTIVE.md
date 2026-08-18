# Genesis 1.3 — Executive Cortex and Cognitive Energy

Genesis 1.3 inserts a deterministic attention/arbitration organ between verified organism physiology and the replaceable LLM brain.

The purpose is not to make another planner. It is to stop asking the expensive model to decide whether the expensive model should have been invoked in the first place.

## Control path

```text
verified persistent state
        ↓
continuity + CRC/vitals
        ↓
metabolism + homeostasis + needs
        ↓
durable goals
        ↓
EXECUTIVE ARBITRATION  ← prior measured brain-seconds
        ├── halt       → no LLM
        ├── hibernate  → no LLM
        ├── maintenance
        ├── resource
        ├── mission
        └── observe
                ↓
        cognitive budget
        tier / token cap / thinking permission / target brain-seconds
                ↓
        replaceable LLM
                ↓
        CriticAssurance
                ↓
        one bounded action
                ↓
        outcome + actual brain-seconds
                ↓
        ExecutiveStore
                └──────────────→ next arbitration
```

## What Executive owns

The Executive may decide:

- whether expensive cognition should wake at all;
- which verified maintenance pressure or durable goal is the current focus;
- whether the cycle is continuity maintenance, resource maintenance, mission work or observation;
- the maximum model token envelope for the cycle;
- whether adaptive deep thinking is permitted;
- the target brain-seconds used for later cost comparison;
- a deterministic sleep/hibernate recommendation when cognition is not justified.

The Executive does **not**:

- grant a capability;
- create a resource or receipt;
- declare an external fact true;
- mark a goal completed;
- choose a concrete side-effecting tool instead of the cognitive/assurance layers;
- bypass CriticAssurance;
- change Subject Core or Continuity Constitution;
- self-certify a body revision.

## Arbitration order

1. Broken Chronicle / critical identity drift is a hard interruption and suppresses normal cognition.
2. Exhausted configured GPU runtime budget suppresses model invocation and hibernates.
3. High verified needs outrank optional mission work.
4. Resource-related needs become `resource` focus but never broaden authority.
5. When maintenance is quiet, durable goals are deterministically ranked. Active goals receive an execution bonus; blocked goals receive a penalty.
6. When neither a verified pressure nor durable goal justifies cognition, hibernation is valid.

The exact thresholds are project configuration in `config/genesis.yaml`, not model text.

## Cognitive tiers

Default policy:

```text
none    0 tokens     no model call
low     256 tokens   no adaptive thinking
normal  640 tokens   no adaptive thinking
deep    1024 tokens  adaptive thinking permitted
```

The configured brain's own maximum remains an outer ceiling. Executive cannot increase it beyond the configured maximum.

The runtime applies token/thinking changes only for one call and restores the original model configuration in `finally`, including when inference raises.

## Cognitive energy feedback

Every Executive decision is recorded **before inference** with a digest of the verified context used for arbitration.

After the cycle, the same row records:

```text
actual brain-seconds
action name
result success/failure
chronicle sequence
next wake
homeostasis mode
```

`CognitiveEnergyController` compares recent target and actual brain-seconds.

It is deliberately one-way conservative:

- repeated overspend can downgrade `deep → normal`;
- severe overspend can downgrade `deep/normal → low`;
- unusually cheap calls do **not** upgrade cognition.

Therefore a measurement error cannot automatically justify spending more compute or acquiring broader authority.

## Feature rollback

`executive.enabled: false` bypasses Executive arbitration inside `ExecutiveOrganismRuntime` and delegates cognition to the Genesis 1.2 metabolic parent runtime.

This is a software feature rollback. It does not create a new identity branch, alter Subject Core, or rewrite lineage.

Genesis 1.2 remains present as `MetabolicOrganismRuntime` and is the direct code-level rollback generation.

## Evidence gates before promotion

Genesis 1.3 is not promoted solely because the module imports. Promotion requires:

- pure arbitration regression tests;
- proof that exhausted compute invokes no brain call;
- proof that cycle-local token/thinking settings are restored;
- measured ExecutiveStore outcome resolution;
- cognitive overspend feedback tests;
- feature-disable rollback test;
- zero-GPU bootstrap through Executive runtime;
- full existing continuity/security regression suite;
- real Chromium/MCP sensorimotor CI lane;
- installed-dependency `pip-audit` gate.

Only after all gates pass should production CLI/MCP status and the canonical body version be treated as Genesis 1.3.
