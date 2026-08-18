from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable
from uuid import uuid4

import yaml

from .provider_context import provider_context


@dataclass(frozen=True, slots=True)
class CognitiveOrganSpec:
    id: str
    name: str
    archetype: str
    objective: str
    attention_bias: str
    search_strategy: str
    preferred_evidence: tuple[str, ...]
    forbidden_shortcuts: tuple[str, ...]
    failure_mode: str
    tags: tuple[str, ...]
    role_classes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        for key in ("preferred_evidence", "forbidden_shortcuts", "tags", "role_classes"):
            item[key] = list(item[key])
        return item


@dataclass(frozen=True, slots=True)
class EpistemicPolicy:
    enabled: bool = True
    trigger_tiers: tuple[str, ...] = ("deep",)
    trigger_on_world_contradiction: bool = True
    normal_quorum: int = 3
    deep_quorum: int = 5
    full_council: bool = False
    per_organ_max_tokens: int = 220
    adjudicator_max_tokens: int = 384
    biography_recent_limit: int = 6
    exploration_weight: float = 0.20
    utility_weight: float = 0.25
    max_public_context_chars: int = 18_000

    def __post_init__(self) -> None:
        if self.normal_quorum < 2 or self.deep_quorum < self.normal_quorum:
            raise ValueError("epistemic quorum sizes must satisfy 2 <= normal <= deep")
        if self.deep_quorum > 12:
            raise ValueError("epistemic deep quorum cannot exceed 12")
        if self.per_organ_max_tokens < 64 or self.adjudicator_max_tokens < 96:
            raise ValueError("epistemic token budgets are too small for evidence packets")
        if self.max_public_context_chars < 2_000:
            raise ValueError("epistemic public context budget is too small")
        for name in ("exploration_weight", "utility_weight"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be finite and within [0, 1]")


@dataclass(frozen=True, slots=True)
class EpistemicPacket:
    id: int | None
    session_id: str
    organ_id: str
    claim: str
    evidence: str
    counterexample: str
    falsifier: str
    uncertainty: str
    confidence: float
    response_fingerprint: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "organ_id": self.organ_id,
            "claim": self.claim,
            "evidence": self.evidence,
            "counterexample": self.counterexample,
            "falsifier": self.falsifier,
            "uncertainty": self.uncertainty,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class EpistemicAdjudication:
    synthesis: str
    selected_packet_ids: tuple[int, ...]
    confidence: float
    disagreements: tuple[str, ...]
    falsification_tests: tuple[str, ...]
    recommended_focus: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "synthesis": self.synthesis,
            "selected_packet_ids": list(self.selected_packet_ids),
            "confidence": self.confidence,
            "disagreements": list(self.disagreements),
            "falsification_tests": list(self.falsification_tests),
            "recommended_focus": self.recommended_focus,
        }


class EpistemicRegistry:
    """Validated Pearson-12 cognitive-organ registry.

    Archetypes are attention/evidence policies, not personas and not independent
    identities. There is one ELIA identity; these are temporary cognitive organs.
    """

    EXPECTED_IDS = {
        "sage",
        "explorer",
        "creator",
        "magician",
        "outlaw",
        "hero",
        "ruler",
        "caregiver",
        "lover",
        "jester",
        "everyman",
        "innocent",
    }

    def __init__(self, policy: EpistemicPolicy, organs: Iterable[CognitiveOrganSpec]):
        self.policy = policy
        self._organs = {item.id: item for item in organs}
        if set(self._organs) != self.EXPECTED_IDS:
            missing = sorted(self.EXPECTED_IDS - set(self._organs))
            extra = sorted(set(self._organs) - self.EXPECTED_IDS)
            raise ValueError(f"epistemic registry must contain Pearson-12 exactly; missing={missing}, extra={extra}")

    @classmethod
    def load(cls, path: Path) -> "EpistemicRegistry":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != 1:
            raise ValueError("unsupported epistemic registry schema")
        raw_policy = dict(payload.get("policy") or {})
        policy = EpistemicPolicy(
            enabled=bool(raw_policy.get("enabled", True)),
            trigger_tiers=tuple(str(item).strip().lower() for item in raw_policy.get("trigger_tiers", ["deep"])),
            trigger_on_world_contradiction=bool(raw_policy.get("trigger_on_world_contradiction", True)),
            normal_quorum=int(raw_policy.get("normal_quorum", 3)),
            deep_quorum=int(raw_policy.get("deep_quorum", 5)),
            full_council=bool(raw_policy.get("full_council", False)),
            per_organ_max_tokens=int(raw_policy.get("per_organ_max_tokens", 220)),
            adjudicator_max_tokens=int(raw_policy.get("adjudicator_max_tokens", 384)),
            biography_recent_limit=int(raw_policy.get("biography_recent_limit", 6)),
            exploration_weight=float(raw_policy.get("exploration_weight", 0.20)),
            utility_weight=float(raw_policy.get("utility_weight", 0.25)),
            max_public_context_chars=int(raw_policy.get("max_public_context_chars", 18_000)),
        )
        organs: list[CognitiveOrganSpec] = []
        seen: set[str] = set()
        for raw in payload.get("organs") or []:
            if not isinstance(raw, dict):
                raise ValueError("epistemic organ entry must be an object")
            organ_id = str(raw.get("id", "")).strip().lower()
            if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", organ_id):
                raise ValueError(f"invalid cognitive organ id: {organ_id!r}")
            if organ_id in seen:
                raise ValueError(f"duplicate cognitive organ id: {organ_id}")
            seen.add(organ_id)
            required_text = {
                key: str(raw.get(key, "")).strip()
                for key in (
                    "name",
                    "archetype",
                    "objective",
                    "attention_bias",
                    "search_strategy",
                    "failure_mode",
                )
            }
            if any(not value for value in required_text.values()):
                raise ValueError(f"cognitive organ {organ_id!r} has an empty required field")
            organs.append(
                CognitiveOrganSpec(
                    id=organ_id,
                    name=required_text["name"][:64],
                    archetype=required_text["archetype"][:128],
                    objective=required_text["objective"][:1000],
                    attention_bias=required_text["attention_bias"][:1500],
                    search_strategy=required_text["search_strategy"][:1500],
                    preferred_evidence=tuple(str(item)[:128] for item in raw.get("preferred_evidence") or []),
                    forbidden_shortcuts=tuple(str(item)[:256] for item in raw.get("forbidden_shortcuts") or []),
                    failure_mode=required_text["failure_mode"][:1000],
                    tags=tuple(str(item).strip().lower()[:64] for item in raw.get("tags") or [] if str(item).strip()),
                    role_classes=tuple(
                        str(item).strip().lower()[:64]
                        for item in raw.get("role_classes") or []
                        if str(item).strip()
                    ),
                )
            )
        return cls(policy, organs)

    def get(self, organ_id: str) -> CognitiveOrganSpec:
        try:
            return self._organs[str(organ_id)]
        except KeyError as exc:
            raise KeyError(f"unknown cognitive organ: {organ_id}") from exc

    def all(self) -> list[CognitiveOrganSpec]:
        return [self._organs[key] for key in sorted(self._organs)]


class CognitiveBiographyStore:
    """Persistent, organ-specific epistemic biography and outcome statistics.

    Outcome association is explicitly operational credit assignment, not proof that an
    organ's claim was true. It exists to let selection adapt while preserving exploration.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS epistemic_sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NULL,
                    mode TEXT NOT NULL,
                    question TEXT NOT NULL,
                    question_digest TEXT NOT NULL,
                    context_digest TEXT NOT NULL,
                    selected_organs_json TEXT NOT NULL,
                    adjudication_json TEXT NULL,
                    action_name TEXT NULL,
                    result_ok INTEGER NULL,
                    outcome_evidence TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_epistemic_sessions_started
                    ON epistemic_sessions(started_at DESC);

                CREATE TABLE IF NOT EXISTS epistemic_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    organ_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    counterexample TEXT NOT NULL,
                    falsifier TEXT NOT NULL,
                    uncertainty TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    response_fingerprint TEXT NOT NULL,
                    supported INTEGER NOT NULL DEFAULT 0,
                    result_ok INTEGER NULL,
                    FOREIGN KEY(session_id) REFERENCES epistemic_sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_epistemic_packets_organ
                    ON epistemic_packets(organ_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_epistemic_packets_session
                    ON epistemic_packets(session_id, id ASC);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _digest(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return sha256(payload.encode("utf-8")).hexdigest()

    def begin_session(
        self,
        *,
        mode: str,
        question: str,
        context_digest: str,
        selected_organs: list[str],
    ) -> str:
        session_id = uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO epistemic_sessions(
                    id, started_at, mode, question, question_digest, context_digest,
                    selected_organs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    self._now(),
                    str(mode)[:64],
                    str(question)[:4000],
                    self._digest(str(question)),
                    str(context_digest)[:128],
                    json.dumps(selected_organs, ensure_ascii=False),
                ),
            )
        return session_id

    def record_packet(self, packet: EpistemicPacket) -> EpistemicPacket:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO epistemic_packets(
                    session_id, organ_id, created_at, claim, evidence, counterexample,
                    falsifier, uncertainty, confidence, response_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    packet.session_id,
                    packet.organ_id,
                    self._now(),
                    packet.claim[:4000],
                    packet.evidence[:5000],
                    packet.counterexample[:3000],
                    packet.falsifier[:3000],
                    packet.uncertainty[:3000],
                    max(0.0, min(1.0, float(packet.confidence))),
                    packet.response_fingerprint,
                ),
            )
            packet_id = int(cur.lastrowid)
        return EpistemicPacket(packet_id, packet.session_id, packet.organ_id, packet.claim, packet.evidence, packet.counterexample, packet.falsifier, packet.uncertainty, packet.confidence, packet.response_fingerprint)

    def finish_adjudication(self, session_id: str, adjudication: EpistemicAdjudication) -> None:
        selected = {int(item) for item in adjudication.selected_packet_ids}
        with self._connect() as conn:
            conn.execute(
                "UPDATE epistemic_sessions SET adjudication_json=? WHERE id=?",
                (json.dumps(adjudication.as_dict(), ensure_ascii=False, sort_keys=True), session_id),
            )
            conn.execute("UPDATE epistemic_packets SET supported=0 WHERE session_id=?", (session_id,))
            if selected:
                placeholders = ",".join("?" for _ in selected)
                conn.execute(
                    f"UPDATE epistemic_packets SET supported=1 WHERE session_id=? AND id IN ({placeholders})",
                    (session_id, *sorted(selected)),
                )

    def resolve_session(
        self,
        session_id: str,
        *,
        result_ok: bool,
        action_name: str,
        outcome_evidence: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE epistemic_sessions
                SET completed_at=?, action_name=?, result_ok=?, outcome_evidence=?
                WHERE id=?
                """,
                (self._now(), str(action_name)[:128], 1 if result_ok else 0, str(outcome_evidence)[:4000], session_id),
            )
            conn.execute(
                "UPDATE epistemic_packets SET result_ok=? WHERE session_id=?",
                (1 if result_ok else 0, session_id),
            )

    def biography(self, organ_id: str, recent_limit: int = 6) -> dict[str, Any]:
        limit = max(0, min(int(recent_limit), 24))
        with self._connect() as conn:
            aggregate = conn.execute(
                """
                SELECT COUNT(*) AS appearances,
                       SUM(CASE WHEN supported=1 THEN 1 ELSE 0 END) AS supported_count,
                       SUM(CASE WHEN result_ok IS NOT NULL THEN 1 ELSE 0 END) AS resolved_count,
                       SUM(CASE WHEN result_ok=1 THEN 1 ELSE 0 END) AS successful_outcomes,
                       AVG(confidence) AS mean_confidence
                FROM epistemic_packets WHERE organ_id=?
                """,
                (str(organ_id),),
            ).fetchone()
            recent = conn.execute(
                """
                SELECT id, session_id, claim, confidence, supported, result_ok
                FROM epistemic_packets WHERE organ_id=?
                ORDER BY id DESC LIMIT ?
                """,
                (str(organ_id), limit),
            ).fetchall()
        appearances = int(aggregate["appearances"] or 0)
        resolved = int(aggregate["resolved_count"] or 0)
        successes = int(aggregate["successful_outcomes"] or 0)
        supported = int(aggregate["supported_count"] or 0)
        return {
            "organ_id": str(organ_id),
            "appearances": appearances,
            "supported_count": supported,
            "support_rate": supported / appearances if appearances else 0.0,
            "resolved_count": resolved,
            "operational_success_rate": successes / resolved if resolved else 0.5,
            "mean_confidence": float(aggregate["mean_confidence"] or 0.5),
            "recent": [
                {
                    "id": int(row["id"]),
                    "session_id": str(row["session_id"]),
                    "claim": str(row["claim"])[:1000],
                    "confidence": float(row["confidence"]),
                    "supported": bool(row["supported"]),
                    "result_ok": (bool(row["result_ok"]) if row["result_ok"] is not None else None),
                }
                for row in recent
            ],
            "epistemic_warning": "Outcome association is operational credit, not proof that this organ's claim was true.",
        }

    def stats(self, organ_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        return {organ_id: self.biography(organ_id, recent_limit=0) for organ_id in organ_ids}

    def recent_sessions(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, started_at, completed_at, mode, question, selected_organs_json,
                       adjudication_json, action_name, result_ok
                FROM epistemic_sessions ORDER BY started_at DESC LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                selected = json.loads(row["selected_organs_json"] or "[]")
            except json.JSONDecodeError:
                selected = []
            try:
                adjudication = json.loads(row["adjudication_json"]) if row["adjudication_json"] else None
            except json.JSONDecodeError:
                adjudication = None
            result.append(
                {
                    "id": str(row["id"]),
                    "started_at": str(row["started_at"]),
                    "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
                    "mode": str(row["mode"]),
                    "question": str(row["question"])[:1000],
                    "selected_organs": selected,
                    "adjudication": adjudication,
                    "action_name": str(row["action_name"]) if row["action_name"] else None,
                    "result_ok": bool(row["result_ok"]) if row["result_ok"] is not None else None,
                }
            )
        return result


_FIELD_NAMES = {
    "claim": "claim",
    "evidence": "evidence",
    "counterexample": "counterexample",
    "falsifier": "falsifier",
    "uncertainty": "uncertainty",
    "confidence": "confidence",
}


def parse_epistemic_packet(text: str, *, session_id: str, organ_id: str) -> EpistemicPacket:
    """Compile free-form divergent output into a bounded evidence packet.

    Divergence is deliberately not requested as JSON. The compiler accepts a compact
    tagged text protocol and preserves conclusions/evidence, never hidden reasoning.
    """
    values: dict[str, str] = {name: "" for name in _FIELD_NAMES.values()}
    current: str | None = None
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z_]+)\s*:\s*(.*)$", line)
        if match and match.group(1).strip().lower() in _FIELD_NAMES:
            current = _FIELD_NAMES[match.group(1).strip().lower()]
            values[current] = match.group(2).strip()
            continue
        if current is not None:
            values[current] = (values[current] + " " + line).strip()
    claim = values["claim"].strip()
    if not claim:
        raise ValueError("epistemic organ response has no CLAIM field")
    try:
        confidence = float(values["confidence"] or 0.5)
    except ValueError:
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    fingerprint = sha256(str(text).encode("utf-8")).hexdigest()
    return EpistemicPacket(
        id=None,
        session_id=session_id,
        organ_id=str(organ_id),
        claim=claim[:4000],
        evidence=values["evidence"][:5000],
        counterexample=values["counterexample"][:3000],
        falsifier=values["falsifier"][:3000],
        uncertainty=values["uncertainty"][:3000],
        confidence=confidence,
        response_fingerprint=fingerprint,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = str(text).strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("adjudicator response did not contain a JSON object")
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("adjudicator response must be a JSON object")
    return value


def parse_adjudication(text: str, valid_packet_ids: set[int]) -> EpistemicAdjudication:
    item = _extract_json_object(text)
    raw_ids = item.get("selected_packet_ids") or []
    selected: list[int] = []
    if isinstance(raw_ids, list):
        for raw in raw_ids[:12]:
            try:
                packet_id = int(raw)
            except (TypeError, ValueError):
                continue
            if packet_id in valid_packet_ids and packet_id not in selected:
                selected.append(packet_id)
    try:
        confidence = float(item.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    disagreements = item.get("disagreements") or []
    falsification = item.get("falsification_tests") or []
    return EpistemicAdjudication(
        synthesis=str(item.get("synthesis", ""))[:5000],
        selected_packet_ids=tuple(selected),
        confidence=max(0.0, min(1.0, confidence)),
        disagreements=tuple(str(value)[:1500] for value in disagreements[:8]) if isinstance(disagreements, list) else (),
        falsification_tests=tuple(str(value)[:1500] for value in falsification[:8]) if isinstance(falsification, list) else (),
        recommended_focus=str(item.get("recommended_focus", ""))[:2000],
    )


class EpistemicCortex:
    """Divergence + neutral evidence adjudication above one replaceable substrate."""

    def __init__(self, registry: EpistemicRegistry, store: CognitiveBiographyStore):
        self.registry = registry
        self.store = store

    @staticmethod
    def _world_has_contradiction(context: dict[str, Any]) -> bool:
        world = context.get("world_model") or {}
        if not isinstance(world, dict):
            return False
        contradictions = world.get("contradictions") or []
        if isinstance(contradictions, list) and contradictions:
            return True
        try:
            return int(world.get("contradiction_count", 0) or 0) > 0
        except (TypeError, ValueError):
            return False

    def should_deliberate(self, context: dict[str, Any]) -> bool:
        if not self.registry.policy.enabled:
            return False
        executive = context.get("executive") or {}
        budget = executive.get("cognitive_budget") if isinstance(executive, dict) else {}
        tier = str((budget or {}).get("tier", "none")).strip().lower()
        if tier in self.registry.policy.trigger_tiers:
            return True
        return self.registry.policy.trigger_on_world_contradiction and self._world_has_contradiction(context)

    @staticmethod
    def _focus_text(context: dict[str, Any]) -> str:
        parts: list[str] = []
        executive = context.get("executive") or {}
        if isinstance(executive, dict):
            focus = executive.get("focus") or {}
            if isinstance(focus, dict):
                parts.extend([str(focus.get("kind", "")), str(focus.get("name", "")), str(focus.get("reason", ""))])
            parts.append(str(executive.get("mode", "")))
        for need in list(context.get("needs") or [])[:8]:
            if isinstance(need, dict):
                parts.extend([str(need.get("name", "")), str(need.get("reason", ""))])
        for goal in list(context.get("active_goals") or [])[:6]:
            if isinstance(goal, dict):
                parts.extend([str(goal.get("title", "")), str(goal.get("description", ""))])
        return " ".join(parts).lower()

    def _selection_score(
        self,
        spec: CognitiveOrganSpec,
        *,
        focus_text: str,
        stats: dict[str, Any],
        total_appearances: int,
    ) -> float:
        relevance = 0.0
        for tag in spec.tags:
            if tag and tag.replace("_", " ") in focus_text:
                relevance += 0.18
        utility = max(0.0, min(1.0, float(stats.get("operational_success_rate", 0.5))))
        appearances = max(0, int(stats.get("appearances", 0)))
        exploration = math.sqrt(math.log(total_appearances + 2.0) / (appearances + 1.0))
        # Utility is intentionally weak: operational success is not epistemic truth.
        return relevance + self.registry.policy.utility_weight * (utility - 0.5) + self.registry.policy.exploration_weight * exploration

    def select_organs(self, context: dict[str, Any]) -> list[CognitiveOrganSpec]:
        all_specs = self.registry.all()
        if self.registry.policy.full_council:
            return all_specs
        executive = context.get("executive") or {}
        budget = executive.get("cognitive_budget") if isinstance(executive, dict) else {}
        tier = str((budget or {}).get("tier", "normal")).strip().lower()
        quorum = self.registry.policy.deep_quorum if tier == "deep" else self.registry.policy.normal_quorum
        quorum = min(max(2, quorum), len(all_specs))
        stats = self.store.stats(spec.id for spec in all_specs)
        total_appearances = sum(int(item.get("appearances", 0)) for item in stats.values())
        focus_text = self._focus_text(context)
        ranked = sorted(
            all_specs,
            key=lambda spec: (
                -self._selection_score(
                    spec,
                    focus_text=focus_text,
                    stats=stats.get(spec.id, {}),
                    total_appearances=total_appearances,
                ),
                spec.id,
            ),
        )
        selected: list[CognitiveOrganSpec] = []

        def add_first(role_class: str) -> None:
            for spec in ranked:
                if role_class in spec.role_classes and spec not in selected:
                    selected.append(spec)
                    return

        # Preserve one evidence anchor and one structural dissenter even after utility adaptation.
        add_first("evidence_anchor")
        add_first("dissent")
        for spec in ranked:
            if spec not in selected:
                selected.append(spec)
            if len(selected) >= quorum:
                break
        return selected[:quorum]

    def _public_context_text(self, context: dict[str, Any]) -> tuple[str, str]:
        public = provider_context(context)
        # The current ecosystem must never recursively ingest its own generated packets.
        public.pop("epistemic", None)
        raw = json.dumps(public, ensure_ascii=False, sort_keys=True, default=str)
        bounded = raw[: self.registry.policy.max_public_context_chars]
        return bounded, sha256(bounded.encode("utf-8")).hexdigest()

    def _question(self, context: dict[str, Any]) -> str:
        executive = context.get("executive") or {}
        focus = executive.get("focus") if isinstance(executive, dict) else {}
        focus_name = str((focus or {}).get("name", "current verified situation"))
        mode = str(executive.get("mode", "observe")) if isinstance(executive, dict) else "observe"
        return (
            f"For the current Executive mode {mode!r} and focus {focus_name!r}, what conclusion or course of action is best supported by the available evidence, and what observation would most strongly falsify it?"
        )[:3000]

    @staticmethod
    def _organ_system_prompt(spec: CognitiveOrganSpec) -> str:
        return f"""You are one temporary cognitive organ inside ELIA WILD: {spec.name} ({spec.archetype}).
You are NOT a separate identity and you do not make the final decision.
Your objective: {spec.objective}
Attention bias: {spec.attention_bias}
Search strategy: {spec.search_strategy}
Preferred evidence: {', '.join(spec.preferred_evidence)}
Forbidden shortcuts: {', '.join(spec.forbidden_shortcuts)}
Known failure mode: {spec.failure_mode}

Do not reveal hidden chain-of-thought. Produce conclusions and evidence only.
Do NOT output JSON. Return exactly these labelled fields in concise plain text:
CLAIM: one substantive conclusion or proposed direction
EVIDENCE: strongest observed support; distinguish observation from inference
COUNTEREXAMPLE: strongest reason this could be wrong
FALSIFIER: one concrete observation/test that would seriously weaken the claim
UNCERTAINTY: the main unresolved uncertainty
CONFIDENCE: a number from 0 to 1
"""

    @staticmethod
    def _adjudicator_system_prompt() -> str:
        return """You are the Epistemic Adjudicator for ELIA WILD.
You are deliberately identity-neutral: you are NOT ELIA's Self, you do not defend its preferred narrative, and you do not reward an organ for agreeing with the majority.
Judge only evidence quality, contradiction handling, falsifiability, uncertainty calibration and relevance to the verified situation.
The packets are conclusions/evidence summaries, not hidden reasoning. Preserve meaningful disagreement instead of forcing consensus.
Return ONLY one JSON object with keys:
{synthesis: string, selected_packet_ids: [int], confidence: 0..1, disagreements: [string], falsification_tests: [string], recommended_focus: string}
"""

    @staticmethod
    def _complete_text(
        brain: Any,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        method = getattr(brain, "complete_text", None)
        if not callable(method):
            raise RuntimeError("current brain substrate does not implement complete_text required by Epistemic Cortex")
        return str(
            method(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=int(max_tokens),
                temperature=float(temperature),
            )
        )

    def deliberate(self, brain: Any, context: dict[str, Any]) -> dict[str, Any]:
        if not self.should_deliberate(context):
            return {
                "enabled": self.registry.policy.enabled,
                "triggered": False,
                "reason": "Executive cognitive tier/world-state did not require multi-perspective deliberation.",
                "biographies": self.biography_snapshot(),
            }
        selected = self.select_organs(context)
        public_context, context_digest = self._public_context_text(context)
        question = self._question(context)
        executive = context.get("executive") or {}
        mode = str(executive.get("mode", "observe")) if isinstance(executive, dict) else "observe"
        session_id = self.store.begin_session(
            mode=mode,
            question=question,
            context_digest=context_digest,
            selected_organs=[item.id for item in selected],
        )
        packets: list[EpistemicPacket] = []
        for spec in selected:
            biography = self.store.biography(spec.id, self.registry.policy.biography_recent_limit)
            user_prompt = (
                f"QUESTION:\n{question}\n\n"
                f"YOUR OWN PRIOR BIOGRAPHY (operational outcomes, not truth labels):\n"
                f"{json.dumps(biography, ensure_ascii=False, sort_keys=True)}\n\n"
                f"CURRENT VERIFIED PUBLIC CONTEXT:\n{public_context}"
            )
            text = self._complete_text(
                brain,
                system_prompt=self._organ_system_prompt(spec),
                user_prompt=user_prompt,
                max_tokens=self.registry.policy.per_organ_max_tokens,
                temperature=0.85,
            )
            packet = parse_epistemic_packet(text, session_id=session_id, organ_id=spec.id)
            packets.append(self.store.record_packet(packet))

        judge_payload = [packet.public_dict() for packet in packets]
        judge_text = self._complete_text(
            brain,
            system_prompt=self._adjudicator_system_prompt(),
            user_prompt=(
                f"QUESTION:\n{question}\n\n"
                f"EPISTEMIC PACKETS:\n{json.dumps(judge_payload, ensure_ascii=False, sort_keys=True)}"
            ),
            max_tokens=self.registry.policy.adjudicator_max_tokens,
            temperature=0.15,
        )
        valid_ids = {int(packet.id) for packet in packets if packet.id is not None}
        adjudication = parse_adjudication(judge_text, valid_ids)
        self.store.finish_adjudication(session_id, adjudication)
        return {
            "enabled": True,
            "triggered": True,
            "session_id": session_id,
            "selected_organs": [spec.id for spec in selected],
            "packets": [packet.public_dict() for packet in packets],
            "adjudication": adjudication.as_dict(),
            "biographies": self.biography_snapshot(selected_only=[spec.id for spec in selected]),
            "epistemic_rule": "Identity is not the judge. Organ diversity is evidence-seeking diversity, not a vote and not multiple identities.",
        }

    def biography_snapshot(self, selected_only: list[str] | None = None) -> dict[str, Any]:
        ids = selected_only or [item.id for item in self.registry.all()]
        return {
            organ_id: self.store.biography(organ_id, self.registry.policy.biography_recent_limit)
            for organ_id in ids
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.registry.policy.enabled,
            "policy": {
                "trigger_tiers": list(self.registry.policy.trigger_tiers),
                "trigger_on_world_contradiction": self.registry.policy.trigger_on_world_contradiction,
                "normal_quorum": self.registry.policy.normal_quorum,
                "deep_quorum": self.registry.policy.deep_quorum,
                "full_council": self.registry.policy.full_council,
            },
            "organs": [
                {
                    "id": spec.id,
                    "name": spec.name,
                    "archetype": spec.archetype,
                    "role_classes": list(spec.role_classes),
                }
                for spec in self.registry.all()
            ],
            "biographies": self.biography_snapshot(),
            "recent_sessions": self.store.recent_sessions(6),
        }
