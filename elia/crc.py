from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from pathlib import Path
from typing import Any, TypeGuard

from . import __version__
from .canonical import strict_json_loads, validate_json_value
from .chronicle import Chronicle
from .config import Config
from .economy import EconomyStore
from .identity import IdentityBundle, IdentityStore
from .memory import MemoryStore
from .prompting import PromptTemplate
from .skills import SkillRegistry
from .tools import ToolRegistry


def _hash_text(value: str) -> str:
    return sha256(str(value).encode("utf-8")).hexdigest()


def _versioned_json(value: Any) -> str:
    """Preserve the CRC v1/v2 byte encoding after strict JSON validation."""

    validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )


CRC_SCHEMA_VERSION = 2
LEGACY_CRC_SCHEMA_VERSION = 1
_HEX_64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ContinuityRecordCapsule:
    schema_version: int
    created_at: str
    identity_id: str
    identity_fingerprint: str
    subject_core_fingerprint: str
    constitution_fingerprint: str
    prompt_fingerprint: str
    branch_id: str
    body_version: str
    brain_backend: str
    model_id: str
    checkpoint_digest: str | None
    checkpoint_counter: int
    chronicle_seq: int
    chronicle_hash: str
    chronicle_valid: bool
    self_model_fingerprint: str | None
    lineage_event_count: int
    lineage_head_id: int | None
    lineage_head_hash: str | None
    lineage_valid: bool
    goal_fingerprints: tuple[str, ...]
    active_goal_count: int
    active_opportunity_count: int
    declared_capabilities: tuple[str, ...]
    available_skills: tuple[str, ...]
    verified_resource_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        for key in ("goal_fingerprints", "declared_capabilities", "available_skills"):
            item[key] = list(item[key])
        return item

    @property
    def fingerprint(self) -> str:
        return _hash_text(_versioned_json(self.as_dict()))


@dataclass(frozen=True, slots=True)
class ContinuityComparison:
    status: str
    score: float
    critical_failures: tuple[str, ...]
    warnings: tuple[str, ...]
    preserved: tuple[str, ...]
    changed: tuple[str, ...]
    evidence_scope: str = "software_continuity_invariants_only"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "critical_failures": list(self.critical_failures),
            "warnings": list(self.warnings),
            "preserved": list(self.preserved),
            "changed": list(self.changed),
            "evidence_scope": self.evidence_scope,
        }


def build_crc(config: Config) -> ContinuityRecordCapsule:
    state_dir = config.runtime.state_dir
    identity = IdentityBundle.load(
        config.subject_core_path,
        config.continuity_constitution_path,
    )
    prompt = PromptTemplate.load(config.system_prompt_path)
    database = state_dir / "memory.sqlite3"
    memory = MemoryStore(database)
    identity_store = IdentityStore(database)
    economy = EconomyStore(database)
    tools = ToolRegistry(state_dir / "workspace", config.raw_tools)
    skills = SkillRegistry(config.skills_dir)
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    chronicle_valid, _ = chronicle.verify()
    chronicle_seq, chronicle_hash = chronicle.head()

    goals = memory.active_goals(10_000)
    goal_fps = tuple(
        sorted(
            _hash_text(
                _versioned_json(
                    {
                        "title": goal.title,
                        "description": goal.description,
                        "status": goal.status,
                        "priority": round(goal.priority, 6),
                        "parent_id": goal.parent_id,
                    }
                )
            )
            for goal in goals
        )
    )
    capability_catalog = tools.catalog()
    capability_health = memory.capability_health_all(list(capability_catalog), window=20)
    skill_state = skills.availability(capability_catalog, capability_health)
    available_skills = tuple(
        sorted(name for name, item in skill_state.items() if item["available"])
    )
    lineage = identity_store.lineage(None)
    head = lineage[-1] if lineage else None
    lineage_valid, _ = identity_store.verify_lineage(
        expected_identity_fingerprint=identity.fingerprint,
        expected_branch_id=config.branch_id,
    )
    resource_summary = economy.resource_summary()
    resource_fp = _hash_text(_versioned_json(resource_summary))

    return ContinuityRecordCapsule(
        schema_version=CRC_SCHEMA_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        identity_id=identity.identity_id,
        identity_fingerprint=identity.fingerprint,
        subject_core_fingerprint=identity.subject_core_fingerprint,
        constitution_fingerprint=identity.constitution_fingerprint,
        prompt_fingerprint=prompt.fingerprint,
        branch_id=config.branch_id,
        body_version=memory.get_meta("body_version", __version__) or __version__,
        brain_backend=config.brain.backend,
        model_id=config.brain.model_id,
        checkpoint_digest=memory.get_meta("checkpoint_digest"),
        checkpoint_counter=int(memory.get_meta("checkpoint_counter", "0") or "0"),
        chronicle_seq=chronicle_seq,
        chronicle_hash=chronicle_hash,
        chronicle_valid=chronicle_valid,
        self_model_fingerprint=memory.get_meta("self_model_fingerprint"),
        lineage_event_count=len(lineage),
        lineage_head_id=head.id if head else None,
        lineage_head_hash=head.event_hash if head else None,
        lineage_valid=lineage_valid,
        goal_fingerprints=goal_fps,
        active_goal_count=len(goals),
        active_opportunity_count=len(economy.active_opportunities(10_000)),
        declared_capabilities=tuple(sorted(capability_catalog)),
        available_skills=available_skills,
        verified_resource_fingerprint=resource_fp,
    )


def write_crc(path: Path, capsule: ContinuityRecordCapsule) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = capsule.as_dict()
    payload["capsule_fingerprint"] = capsule.fingerprint
    errors = validate_crc_capsule(payload, require_capsule_fingerprint=True)
    if errors:
        raise ValueError("invalid CRC capsule: " + "; ".join(errors))
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def read_crc(path: Path) -> dict[str, Any]:
    item = strict_json_loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(item, dict):
        raise ValueError("CRC file must contain a JSON object")
    errors = validate_crc_capsule(item, require_capsule_fingerprint=True)
    if errors:
        raise ValueError("invalid CRC capsule: " + "; ".join(errors))
    return item


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and _HEX_64.fullmatch(value) is not None


def validate_crc_capsule(
    item: dict[str, Any],
    *,
    require_capsule_fingerprint: bool = False,
) -> tuple[str, ...]:
    """Validate a serialized CRC before it can participate in continuity evidence."""

    errors: list[str] = []
    try:
        validate_json_value(item)
    except (TypeError, ValueError) as exc:
        return (f"capsule is not strict finite JSON: {exc}",)
    schema_version = item.get("schema_version")
    current_required = {
        field.name for field in ContinuityRecordCapsule.__dataclass_fields__.values()
    }
    required = (
        current_required - {"lineage_head_hash", "lineage_valid"}
        if schema_version == LEGACY_CRC_SCHEMA_VERSION
        else current_required
    )
    missing = sorted(required - set(item))
    if missing:
        errors.append("missing required fields: " + ", ".join(missing))

    if not _is_int(schema_version) or schema_version not in {
        LEGACY_CRC_SCHEMA_VERSION,
        CRC_SCHEMA_VERSION,
    }:
        errors.append(
            f"schema_version must equal {LEGACY_CRC_SCHEMA_VERSION} or {CRC_SCHEMA_VERSION}"
        )

    created_at = item.get("created_at")
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("timezone missing")
    except (TypeError, ValueError):
        errors.append("created_at must be a timezone-aware ISO-8601 timestamp")

    for field in ("identity_id", "branch_id", "body_version", "brain_backend", "model_id"):
        if not isinstance(item.get(field), str) or not str(item.get(field)).strip():
            errors.append(f"{field} must be a non-empty string")

    for field in (
        "identity_fingerprint",
        "subject_core_fingerprint",
        "constitution_fingerprint",
        "prompt_fingerprint",
        "chronicle_hash",
        "verified_resource_fingerprint",
    ):
        if not _is_hash(item.get(field)):
            errors.append(f"{field} must be a lowercase SHA-256 digest")

    optional_hash_fields = ["checkpoint_digest", "self_model_fingerprint"]
    if schema_version == CRC_SCHEMA_VERSION:
        optional_hash_fields.append("lineage_head_hash")
    for field in optional_hash_fields:
        if item.get(field) is not None and not _is_hash(item.get(field)):
            errors.append(f"{field} must be null or a lowercase SHA-256 digest")

    for field in (
        "checkpoint_counter",
        "chronicle_seq",
        "lineage_event_count",
        "active_goal_count",
        "active_opportunity_count",
    ):
        if not _is_int(item.get(field)) or int(item.get(field, -1)) < 0:
            errors.append(f"{field} must be a non-negative integer")

    if not isinstance(item.get("chronicle_valid"), bool):
        errors.append("chronicle_valid must be boolean")
    if schema_version == CRC_SCHEMA_VERSION and not isinstance(item.get("lineage_valid"), bool):
        errors.append("lineage_valid must be boolean")

    lineage_count = item.get("lineage_event_count")
    lineage_head = item.get("lineage_head_id")
    if _is_int(lineage_count):
        if lineage_count == 0 and lineage_head is not None:
            errors.append("lineage_head_id must be null when lineage_event_count is zero")
        if (
            schema_version == CRC_SCHEMA_VERSION
            and lineage_count == 0
            and item.get("lineage_head_hash") is not None
        ):
            errors.append("lineage_head_hash must be null when lineage_event_count is zero")
        if lineage_count > 0 and (not _is_int(lineage_head) or int(lineage_head) < 1):
            errors.append("lineage_head_id must be positive when lineage events exist")
        if (
            schema_version == CRC_SCHEMA_VERSION
            and lineage_count > 0
            and not _is_hash(item.get("lineage_head_hash"))
        ):
            errors.append("lineage_head_hash is required when lineage events exist")

    checkpoint_counter = item.get("checkpoint_counter")
    checkpoint_digest = item.get("checkpoint_digest")
    if _is_int(checkpoint_counter):
        if checkpoint_counter == 0 and checkpoint_digest is not None:
            errors.append("checkpoint_digest must be null when checkpoint_counter is zero")
        if checkpoint_counter > 0 and checkpoint_digest is None:
            errors.append("checkpoint_digest is required when checkpoint_counter is positive")

    for field in ("goal_fingerprints", "declared_capabilities", "available_skills"):
        value = item.get(field)
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(entry, str) or not entry for entry in (value or [])
        ):
            errors.append(f"{field} must be a list of non-empty strings")
    goals = item.get("goal_fingerprints")
    if isinstance(goals, (list, tuple)):
        if any(not _is_hash(value) for value in goals):
            errors.append("goal_fingerprints entries must be lowercase SHA-256 digests")
        if len(set(goals)) != len(goals):
            errors.append("goal_fingerprints must be unique")
        if _is_int(item.get("active_goal_count")) and len(goals) != item["active_goal_count"]:
            errors.append("active_goal_count does not match goal_fingerprints length")

    fingerprint = item.get("capsule_fingerprint")
    if require_capsule_fingerprint and not _is_hash(fingerprint):
        errors.append("capsule_fingerprint is required and must be a lowercase SHA-256 digest")
    if fingerprint is not None and _is_hash(fingerprint):
        payload = {key: value for key, value in item.items() if key != "capsule_fingerprint"}
        expected = _hash_text(_versioned_json(payload))
        if fingerprint != expected:
            errors.append("capsule_fingerprint does not match the capsule payload")

    return tuple(errors)


def compare_crc(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    chronicle: Chronicle | None = None,
    require_ancestry: bool = False,
    lineage_store: IdentityStore | None = None,
    require_lineage_ancestry: bool = False,
) -> ContinuityComparison:
    """Compare continuity capsules, optionally proving exact Chronicle ancestry.

    Sequence monotonicity alone is only a weak compatibility check. Production vital
    signs pass the live Chronicle and require ancestry, which proves that the previous
    accepted `(seq, hash)` remains an exact prefix anchor of the current chain.
    """
    critical: list[str] = []
    warnings: list[str] = []
    preserved: list[str] = []
    changed: list[str] = []
    score = 0.0

    left_errors = validate_crc_capsule(left)
    right_errors = validate_crc_capsule(right)
    if left_errors or right_errors:
        critical.extend(f"left capsule invalid: {error}" for error in left_errors)
        critical.extend(f"right capsule invalid: {error}" for error in right_errors)
        return ContinuityComparison(
            status="broken",
            score=0.0,
            critical_failures=tuple(critical),
            warnings=(),
            preserved=(),
            changed=(),
        )

    left_schema = int(left["schema_version"])
    right_schema = int(right["schema_version"])
    if right_schema < left_schema:
        critical.append("CRC schema version moved backward")
        changed.append("schema_version")
    elif right_schema > left_schema:
        changed.append("schema_version")
        warnings.append(
            f"CRC evidence schema upgraded from {left_schema} to {right_schema}"
        )

    for field, weight in (
        ("identity_id", 0.10),
        ("identity_fingerprint", 0.30),
        ("subject_core_fingerprint", 0.10),
        ("constitution_fingerprint", 0.10),
        ("branch_id", 0.10),
    ):
        if left.get(field) == right.get(field):
            preserved.append(field)
            score += weight
        else:
            critical.append(f"{field} changed")
            changed.append(field)

    left_seq = int(left.get("chronicle_seq", 0) or 0)
    right_seq = int(right.get("chronicle_seq", 0) or 0)
    left_hash = str(left.get("chronicle_hash", "")).strip().lower()
    right_hash = str(right.get("chronicle_hash", "")).strip().lower()
    if not bool(left.get("chronicle_valid")):
        critical.append("left accepted Chronicle baseline is invalid")
    if not bool(right.get("chronicle_valid")):
        critical.append("right Chronicle is invalid")
    elif right_seq < left_seq:
        critical.append("Chronicle sequence moved backward")
    elif right_seq == left_seq and right_hash != left_hash:
        critical.append("Chronicle hash changed at an unchanged sequence")
    elif chronicle is not None:
        anchor_ok, anchor_error = chronicle.contains_anchor(left_seq, left_hash)
        if anchor_ok:
            preserved.append("chronicle_prefix_ancestry")
            score += 0.10
        else:
            critical.append(
                "Chronicle prefix ancestry failed: "
                + (anchor_error or "previous accepted head is not an ancestor")
            )
    elif require_ancestry:
        critical.append("Chronicle ancestry proof was required but no Chronicle was supplied")
    else:
        # Backward-compatible weak comparison for offline/synthetic callers. VitalSigns
        # never uses this branch.
        preserved.append("chronicle_monotonicity_unproven")
        warnings.append(
            "Chronicle sequence is monotonic but exact prefix ancestry was not proven"
        )
        score += 0.10

    left_checkpoint_counter = int(left["checkpoint_counter"])
    right_checkpoint_counter = int(right["checkpoint_counter"])
    if right_checkpoint_counter < left_checkpoint_counter:
        critical.append("checkpoint counter moved backward")
        changed.append("checkpoint_counter")
    elif (
        right_checkpoint_counter == left_checkpoint_counter
        and left.get("checkpoint_digest") != right.get("checkpoint_digest")
    ):
        critical.append("checkpoint digest changed at an unchanged counter")
        changed.append("checkpoint_digest")
    elif (
        right_checkpoint_counter > left_checkpoint_counter
        and left.get("checkpoint_digest") is not None
        and left.get("checkpoint_digest") == right.get("checkpoint_digest")
    ):
        critical.append("checkpoint counter advanced without a new digest")
        changed.append("checkpoint_counter")
    else:
        preserved.append("checkpoint_monotonicity")

    left_lineage_count = int(left["lineage_event_count"])
    right_lineage_count = int(right["lineage_event_count"])
    left_has_hash_chain = left_schema >= CRC_SCHEMA_VERSION
    right_has_hash_chain = right_schema >= CRC_SCHEMA_VERSION
    if left_has_hash_chain and not bool(left["lineage_valid"]):
        critical.append("left accepted identity lineage baseline is invalid")
    if right_has_hash_chain and not bool(right["lineage_valid"]):
        critical.append("right identity lineage is invalid")
    elif not right_has_hash_chain:
        warnings.append("right legacy CRC does not attest lineage hash-chain validity")

    lineage_structure_ok = True
    if right_lineage_count < left_lineage_count:
        lineage_structure_ok = False
        critical.append("lineage event count moved backward")
        changed.append("lineage_event_count")
    elif (
        right_lineage_count == left_lineage_count
        and left.get("lineage_head_id") != right.get("lineage_head_id")
    ):
        lineage_structure_ok = False
        critical.append("lineage head changed at an unchanged event count")
        changed.append("lineage_head_id")
    elif (
        right_lineage_count == left_lineage_count
        and left_has_hash_chain
        and right_has_hash_chain
        and left.get("lineage_head_hash") != right.get("lineage_head_hash")
    ):
        lineage_structure_ok = False
        critical.append("lineage hash changed at an unchanged event count")
        changed.append("lineage_head_hash")
    elif (
        right_lineage_count > left_lineage_count
        and left.get("lineage_head_id") is not None
        and int(right["lineage_head_id"]) <= int(left["lineage_head_id"])
    ):
        lineage_structure_ok = False
        critical.append("lineage count advanced without a monotonic head id")
        changed.append("lineage_head_id")

    if lineage_structure_ok and right_lineage_count == left_lineage_count:
        preserved.append(
            "lineage_exact_head"
            if left_has_hash_chain and right_has_hash_chain
            else "legacy_lineage_head_id"
        )
    elif lineage_structure_ok and left_lineage_count == 0:
        preserved.append("lineage_genesis_ancestry")
    elif lineage_structure_ok and left_has_hash_chain:
        if lineage_store is not None:
            anchor_id = int(left["lineage_head_id"])
            anchor_hash = str(left["lineage_head_hash"])
            anchor = next(
                (event for event in lineage_store.lineage(None) if event.id == anchor_id),
                None,
            )
            if anchor is not None and anchor.event_hash == anchor_hash:
                preserved.append("lineage_prefix_ancestry")
            else:
                critical.append(
                    "identity lineage prefix ancestry failed: previous accepted head is not present"
                )
        elif require_lineage_ancestry:
            critical.append(
                "identity lineage ancestry proof was required but no IdentityStore was supplied"
            )
        else:
            preserved.append("lineage_monotonicity_unproven")
            warnings.append(
                "lineage count/head are monotonic but exact hash-prefix ancestry was not proven"
            )
    elif lineage_structure_ok:
        # A v1 baseline bound only the lineage count/head id. Permit one upgrade cycle
        # without pretending that legacy evidence contained a hash anchor it never had.
        preserved.append("legacy_lineage_monotonicity_unproven")
        warnings.append(
            "legacy CRC had no lineage head hash; exact prior lineage ancestry is unprovable"
        )

    left_goals = set(left.get("goal_fingerprints") or [])
    right_goals = set(right.get("goal_fingerprints") or [])
    if left_goals:
        overlap = len(left_goals & right_goals) / len(left_goals)
    else:
        overlap = 1.0
    score += 0.05 * overlap
    if overlap < 0.5:
        warnings.append(
            f"less than half of prior active goal fingerprints remain ({overlap:.2f})"
        )
    else:
        preserved.append("goal_continuity")

    left_capabilities = set(left.get("declared_capabilities") or [])
    right_capabilities = set(right.get("declared_capabilities") or [])
    if left_capabilities <= right_capabilities:
        preserved.append("capability_superset")
        score += 0.05
    else:
        warnings.append("one or more previously declared capabilities disappeared")

    # Model/backend/body/prompt changes are observable mutations, not automatic death.
    for field in ("model_id", "brain_backend", "body_version", "prompt_fingerprint"):
        if left.get(field) != right.get(field):
            changed.append(field)
            warnings.append(f"substrate/body field changed: {field}")

    for field, label in (
        ("self_model_fingerprint", "adaptive self-model field changed"),
        ("verified_resource_fingerprint", "verified resource state changed"),
    ):
        if left.get(field) != right.get(field):
            changed.append(field)
            warnings.append(label)

    score = max(0.0, min(1.0, score))
    status = "broken" if critical else "continuous" if score >= 0.80 else "uncertain"
    return ContinuityComparison(
        status=status,
        score=score,
        critical_failures=tuple(critical),
        warnings=tuple(warnings),
        preserved=tuple(preserved),
        changed=tuple(sorted(set(changed))),
    )
