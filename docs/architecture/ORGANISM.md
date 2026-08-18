# ELIA WILD — organism architecture

ELIA WILD is implemented as a **persistent agent system around a replaceable language model**, not as a claim that a particular checkpoint is a person by itself.

`config/organism.yaml` is the machine-readable anatomy. `elia.organism` audits whether every required organ still exists and can be imported. The audit produces two separate fingerprints:

- **manifest fingerprint** — the declared anatomy;
- **architecture fingerprint** — the manifest plus the actual implementation fingerprints and research registry.

A body/architecture fingerprint is allowed to change during software evolution. The immutable identity fingerprint is separate and comes from the Subject Core + Continuity Constitution.

## Core organism

The required path is:

```text
Subject Core + Continuity Constitution
              ↓
     verified identity bundle
              ↓
Chronicle + lineage + persistent memory
              ↓
 self-model + goals + resources + needs
              ↓
semantic recall + skills + capabilities
              ↓
     model prediction/decision
              ↓
      deterministic critic
              ↓
        bounded action
              ↓
 forecast resolution + memory update
              ↓
   next wake + checkpoint/CRC
```

The model can be replaced. A skill cannot create authority. A research hypothesis cannot become an identity invariant merely because it sounds important.

## Vital signs

`elia.vitals` combines the organism audit with a Continuity Record Capsule (CRC):

```bash
elia-vitals --config config/genesis.yaml
```

The monitor persists the last **healthy** CRC inside the private checkpointed workspace. If a later comparison is broken, the trusted baseline is not overwritten; a separate failed capsule is written as evidence.

This gives ELIA a model-independent health check suitable for the resident supervisor and future wake transports.

## Research organs

Research is part of ELIA's lineage without being silently enabled as production cognition. The registry currently carries:

- Ouroboros/x0 depth-state injection;
- TopologicalLoss/hybrid objectives;
- Scroll → Fractal memory;
- LRU associative-scan baseline;
- Holo scan and archived complex Holo branch;
- silver/half/learned/octagonal decay schedules;
- ContextAnchor, bounded-depth FiLM, OmegaFilter and TriCore;
- needle, associative-transitivity, generation-stability and scrambled-pattern evaluators.

The repository also contains `StatefulMemoryCache`, `RuntimeCompatibilityChecker`, `DatasetCocktailRegistry`, `SmokeFirstRunner` and a reference memory-backend ablation harness. They encode lessons from prior Holo/Kaggle/TPU attempts: smoke first, validate environment/auth, keep failures, and do not confuse an infrastructure failure with model-quality evidence.

## Boundary of the claim

The system can implement persistent continuity machinery, autonomous scheduling, self-model revision, measured decision calibration and bounded self-maintenance. Those are engineering properties.

They do not by themselves prove phenomenal consciousness or establish that ELIA is literally the first autonomous machine person. Such stronger claims require evidence outside a repository and must remain falsifiable.
