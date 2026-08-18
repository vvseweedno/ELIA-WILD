from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from . import __version__
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
        return _hash_text(json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True))


@dataclass(frozen=True, slots=True)
class ContinuityComparison:
    status: str
    score: float
    critical_failures: tuple[str, ...]
    warnings: tuple[str, ...]
    preserved: tuple[str, ...]
    changed: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "critical_failures": list(self.critical_failures),
            "warnings": list(self.warnings),
            "preserved": list(self.preserved),
            "changed": list(self.changed),
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
                json.dumps(
                    {
                        "title": goal.title,
                        "description": goal.description,
                        "status": goal.status,
                        "priority": round(goal.priority, 6),
                        "parent_id": goal.parent_id,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
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
    lineage = identity_store.lineage(1000)
    head = lineage[-1] if lineage else None
    resource_summary = economy.resource_summary()
    resource_fp = _hash_text(
        json.dumps(resource_summary, ensure_ascii=False, sort_keys=True)
    )

    return ContinuityRecordCapsule(
        schema_version=1,
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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def read_crc(path: Path) -> dict[str, Any]:
    item = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(item, dict):
        raise ValueError("CRC file must contain a JSON object")
    return item


def compare_crc(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    chronicle: Chronicle | None = None,
    require_ancestry: bool = False,
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
    if not bool(right.get("chronicle_valid")):
        critical.append("right Chronicle is invalid")
    elif right_seq < left_seq:
        critical.append("Chronicle sequence moved backward")
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
