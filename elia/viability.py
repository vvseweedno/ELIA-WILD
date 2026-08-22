from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from .brain import MockBrain
from .chronicle import Chronicle
from .config import Config
from .continuity_runtime import ELIARuntime
from .crc import build_crc
from .lifecycle import evaluate_preflight
from .memory import MemoryStore
from .metabolism import MetabolismEngine, MetabolismStore
from .organism import OrganSpec, OrganismManifest
from .transition_kernel import AcceptedTransitionGuard
from .verification import VerificationRegistry, consume_verified_receipt


RUNTIME_WIRING: dict[str, str] = {
    "identity_bundle": "identity",
    "identity_lineage": "identity_store",
    "chronicle": "chronicle",
    "chronicle_prefix_ancestry": "chronicle",
    "persistent_memory": "memory",
    "semantic_recall": "recall",
    "adaptive_self_model": "self_hypotheses",
    "observation_store": "tools.observations",
    "world_model": "tools.world_model",
    "causal_memory": "tools.causal",
    "organism_state_bus": "tools.state_bus",
    "sensorimotor_fabric": "tools.body",
    "metacognition": "metacognition",
    "prompt_renderer": "prompt_template",
    "critic_assurance": "assurance",
    "identity_drift_monitor": "drift_monitor",
    "capability_registry": "tools",
    "skill_registry": "skills",
    "executive_controller": "executive",
    "executive_store": "executive_store",
    "cognitive_energy_controller": "cognitive_energy",
    "economy": "economy",
    "resource_ecology_store": "resource_ecology_store",
    "resource_ecology_engine": "resource_ecology",
    "work_port_store": "work_ports.store",
    "work_port_outbox": "work_ports.store",
    "work_port_registry": "work_ports",
    "cognitive_biographies": "epistemic_store",
    "selective_credit_store": "epistemic_store",
    "epistemic_view_store": "epistemic_view_store",
    "resilient_epistemic_cortex": "epistemic_cortex",
    "epistemic_security_boundary": "epistemic_cortex",
}

RUNTIME_ANCESTRY = {
    "genesis_runtime": "EliaRuntime",
    "organism_runtime": "OrganismRuntime",
    "metabolic_runtime": "MetabolicOrganismRuntime",
    "executive_runtime": "ExecutiveOrganismRuntime",
    "resource_runtime": "ResourceOrganismRuntime",
    "external_work_runtime": "ExternalWorkOrganismRuntime",
    "epistemic_runtime": "EpistemicOrganismRuntime",
    "continuity_kernel_runtime": "ELIARuntime",
}


@dataclass(frozen=True, slots=True)
class ViabilityContract:
    organ_id: str
    producer: str
    consumer: str
    runtime_path: str
    state_owned: str
    read_authority: str
    write_authority: str
    health_probe: str
    persistence_probe: str
    recovery_probe: str
    expected_evidence: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ViabilityProbe:
    organ_id: str
    ok: bool
    probe: str
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ViabilityReport:
    healthy: bool
    runtime_class: str
    required_organ_count: int
    contract_count: int
    probes: tuple[ViabilityProbe, ...]
    contracts: tuple[ViabilityContract, ...]
    persistence: dict[str, Any]
    recovery: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "runtime_class": self.runtime_class,
            "required_organ_count": self.required_organ_count,
            "contract_count": self.contract_count,
            "probes": [item.as_dict() for item in self.probes],
            "contracts": [item.as_dict() for item in self.contracts],
            "persistence": self.persistence,
            "recovery": self.recovery,
        }


def _contract(organ: OrganSpec) -> ViabilityContract:
    runtime_path = RUNTIME_WIRING.get(organ.id)
    if runtime_path is None and organ.id in RUNTIME_ANCESTRY:
        runtime_path = f"ELIARuntime.mro:{RUNTIME_ANCESTRY[organ.id]}"
    if runtime_path is None:
        runtime_path = (
            organ.path or "<missing artifact path>"
            if organ.kind == "artifact"
            else f"{organ.module}.{organ.symbol or '<module>'}"
        )
    stateful = "state" in organ.authority or organ.layer in {
        "memory",
        "world",
        "metabolism",
        "resource_ecology",
        "external_work",
        "continuity_kernel",
    }
    producer = (
        "deployment artifact"
        if organ.kind == "artifact"
        else f"{organ.module}.{organ.symbol or '<module>'}"
    )
    consumer = (
        "ELIARuntime production graph"
        if organ.id in RUNTIME_WIRING or organ.id in RUNTIME_ANCESTRY
        else "configured runtime / deterministic lifecycle"
    )
    return ViabilityContract(
        organ_id=organ.id,
        producer=producer,
        consumer=consumer,
        runtime_path=runtime_path,
        state_owned=("governed organism state" if stateful else "none / derived state"),
        read_authority=organ.authority or "none",
        write_authority=(organ.authority if stateful else "none"),
        health_probe=(
            "production runtime attribute resolution"
            if organ.id in RUNTIME_WIRING
            else (
                "production runtime MRO ancestry"
                if organ.id in RUNTIME_ANCESTRY
                else "manifest artifact/import fingerprint"
            )
        ),
        persistence_probe=(
            "scratch SQLite reconnect / domain snapshot"
            if stateful
            else "not applicable"
        ),
        recovery_probe=(
            "accepted-transition rollback to prior SQLite+Chronicle head"
            if stateful
            else "not applicable"
        ),
        expected_evidence=(
            "resolved runtime object plus successful persistence/recovery gate"
            if organ.id in RUNTIME_WIRING
            else "manifest fingerprint and deterministic semantic probe"
        ),
    )


def viability_contracts(manifest: OrganismManifest) -> tuple[ViabilityContract, ...]:
    return tuple(_contract(organ) for organ in manifest.organs if organ.required)


def _resolve_attr(root: Any, dotted: str) -> Any:
    value = root
    for part in dotted.split("."):
        value = getattr(value, part)
    return value


def _scratch_config(config: Config, state_dir: Path) -> Config:
    tools = deepcopy(config.raw_tools)
    work_ports = dict(tools.get("work_ports") or {})
    work_ports["enabled"] = False
    work_ports["ports"] = {}
    tools["work_ports"] = work_ports
    body = dict(tools.get("body") or {})
    for name in ("browser", "process", "mcp", "jsonrpc"):
        item = dict(body.get(name) or {})
        item["enabled"] = False
        body[name] = item
    tools["body"] = body
    return replace(
        config,
        runtime=replace(
            config.runtime,
            state_dir=state_dir,
            auto_checkpoint_path=None,
        ),
        raw_tools=tools,
    )


def _persistence_probe(runtime: ELIARuntime) -> dict[str, Any]:
    runtime.memory.set_meta("deep_viability_sentinel", "accepted")
    database = runtime.config.runtime.state_dir / "memory.sqlite3"
    reloaded = MemoryStore(database)
    meta_ok = reloaded.get_meta("deep_viability_sentinel") == "accepted"

    observation = runtime.tools.observations.record(
        source_kind="viability",
        source_ref="deep_probe",
        payload={"sentinel": "sensorium"},
        trust=1.0,
        success=True,
        summary="deep viability sensorium persistence probe",
        provenance={"producer": "elia-vitals --deep"},
    )
    read_back = runtime.tools.observations.get(observation.id)
    observation_ok = (
        read_back is not None
        and read_back.payload == {"sentinel": "sensorium"}
        and read_back.payload_sha256 == observation.payload_sha256
    )

    metabolism_store = MetabolismStore(database)
    metabolism_engine = MetabolismEngine(
        database,
        weekly_gpu_budget_hours=runtime.config.runtime.weekly_gpu_budget_hours,
    )
    metabolism_ok = isinstance(metabolism_store, MetabolismStore) and isinstance(
        metabolism_engine.snapshot().as_dict(), dict
    )

    preflight = evaluate_preflight(
        runtime.config.runtime.state_dir,
        runtime.config.runtime.weekly_gpu_budget_hours,
        expected_identity_fingerprint=runtime.identity.fingerprint,
        expected_branch_id=runtime.config.branch_id,
    )
    crc = build_crc(runtime.config)
    return {
        "ok": bool(
            meta_ok
            and observation_ok
            and metabolism_ok
            and crc.identity_fingerprint == runtime.identity.fingerprint
            and preflight.mode in {"wake", "hibernate", "halt"}
        ),
        "sqlite_reconnect": meta_ok,
        "sensorium_verified_read": observation_ok,
        "metabolism_snapshot": metabolism_ok,
        "crc_identity_match": crc.identity_fingerprint == runtime.identity.fingerprint,
        "preflight_mode": preflight.mode,
    }


def _verification_probe(database: Path) -> dict[str, Any]:
    registry = VerificationRegistry({"deep-vitals": b"v" * 32})
    claim = {"kind": "deep_viability", "value": 1}
    evidence = "synthetic deep-vitals evidence"
    receipt = registry.issue(
        "deep-vitals",
        claim=claim,
        evidence=evidence,
        nonce="deep-vitals-single-use",
    )
    with sqlite3.connect(database, timeout=30.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        consume_verified_receipt(
            conn,
            registry,
            receipt,
            claim=claim,
            evidence=evidence,
            purpose="deep_viability",
            subject_ref="scratch",
        )
    replay_blocked = False
    try:
        with sqlite3.connect(database, timeout=30.0) as conn:
            conn.execute("BEGIN IMMEDIATE")
            consume_verified_receipt(
                conn,
                registry,
                receipt,
                claim=claim,
                evidence=evidence,
                purpose="deep_viability",
                subject_ref="scratch-replay",
            )
    except PermissionError:
        replay_blocked = True
    return {"ok": replay_blocked, "single_use_replay_blocked": replay_blocked}


def _recovery_probe(runtime: ELIARuntime) -> dict[str, Any]:
    state_dir = runtime.config.runtime.state_dir
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    before_seq, before_hash = chronicle.head()
    runtime.memory.set_meta("deep_transition_state", "accepted")
    recovered = False
    try:
        with AcceptedTransitionGuard(state_dir, chronicle):
            runtime.memory.set_meta("deep_transition_state", "speculative")
            chronicle.append(
                "DEEP_VIABILITY_SPECULATIVE",
                {"must_survive": False},
            )
            raise RuntimeError("intentional deep viability rollback")
    except RuntimeError as exc:
        if "intentional deep viability rollback" not in str(exc):
            raise
        recovered = True
    after_seq, after_hash = chronicle.head()
    restored_meta = MemoryStore(state_dir / "memory.sqlite3").get_meta(
        "deep_transition_state"
    )
    ok = bool(
        recovered
        and restored_meta == "accepted"
        and (after_seq, after_hash) == (before_seq, before_hash)
        and not (state_dir / "transition-kernel" / "active.json").exists()
    )
    return {
        "ok": ok,
        "rollback_triggered": recovered,
        "sqlite_projection_restored": restored_meta == "accepted",
        "chronicle_head_restored": (after_seq, after_hash) == (before_seq, before_hash),
        "journal_cleared": not (state_dir / "transition-kernel" / "active.json").exists(),
        "accepted_head": {"seq": before_seq, "hash": before_hash},
    }


def run_deep_viability(config: Config, manifest: OrganismManifest) -> ViabilityReport:
    contracts = viability_contracts(manifest)
    required = [organ for organ in manifest.organs if organ.required]
    probes: list[ViabilityProbe] = []

    with tempfile.TemporaryDirectory(prefix="elia-deep-vitals-") as temp:
        scratch_state = Path(temp) / ".elia"
        runtime = ELIARuntime(
            _scratch_config(config, scratch_state),
            brain=MockBrain(),
        )

        for organ in required:
            runtime_path = RUNTIME_WIRING.get(organ.id)
            if runtime_path is None:
                continue
            try:
                value = _resolve_attr(runtime, runtime_path)
                probes.append(
                    ViabilityProbe(
                        organ.id,
                        value is not None,
                        "runtime_wiring",
                        f"resolved {runtime_path} -> {type(value).__name__}",
                    )
                )
            except Exception as exc:
                probes.append(
                    ViabilityProbe(
                        organ.id,
                        False,
                        "runtime_wiring",
                        f"{type(exc).__name__}: {str(exc)[:500]}",
                    )
                )

        mro_names = {cls.__name__ for cls in type(runtime).mro()}
        for organ_id, class_name in RUNTIME_ANCESTRY.items():
            if not any(organ.id == organ_id and organ.required for organ in required):
                continue
            probes.append(
                ViabilityProbe(
                    organ_id,
                    class_name in mro_names,
                    "runtime_ancestry",
                    f"required class {class_name}; mro={sorted(mro_names)}",
                )
            )

        persistence = _persistence_probe(runtime)
        verification = _verification_probe(
            runtime.config.runtime.state_dir / "memory.sqlite3"
        )
        recovery = _recovery_probe(runtime)
        probes.append(
            ViabilityProbe(
                "verification_consumption_kernel",
                bool(verification["ok"]),
                "single_use_verification",
                json.dumps(verification, sort_keys=True),
            )
        )
        probes.append(
            ViabilityProbe(
                "accepted_transition_guard",
                bool(recovery["ok"]),
                "fault_recovery",
                json.dumps(recovery, sort_keys=True),
            )
        )

    contract_complete = len(contracts) == len(required) and all(
        all(
            str(getattr(item, field)).strip()
            for field in (
                "producer",
                "consumer",
                "runtime_path",
                "state_owned",
                "read_authority",
                "write_authority",
                "health_probe",
                "persistence_probe",
                "recovery_probe",
                "expected_evidence",
            )
        )
        for item in contracts
    )
    healthy = bool(
        contract_complete
        and probes
        and all(item.ok for item in probes)
        and persistence.get("ok")
        and recovery.get("ok")
    )
    return ViabilityReport(
        healthy=healthy,
        runtime_class=ELIARuntime.__name__,
        required_organ_count=len(required),
        contract_count=len(contracts),
        probes=tuple(probes),
        contracts=contracts,
        persistence={**persistence, "verification": verification},
        recovery=recovery,
    )
