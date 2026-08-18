from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from .epistemic import (
    CognitiveBiographyStore,
    CognitiveOrganSpec,
    EpistemicAdjudication,
    EpistemicCortex,
    EpistemicPacket,
    EpistemicRegistry,
    parse_adjudication,
    parse_epistemic_packet,
)
from .provider_context import provider_context


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return str(value)[:500]
    if isinstance(value, str):
        return value[:1800]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_bounded(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, tuple):
        return [_bounded(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item))[:48]:
            result[str(key)[:128]] = _bounded(value[key], depth=depth + 1)
        return result
    return str(value)[:1000]


class EvidenceViewProjector:
    """Create different sanitized evidence diets for temporary cognitive organs.

    Differentiation is based on data exposure as well as prompt policy. Every view is
    derived from `provider_context`, so an organ cannot bypass the remote-provider
    privacy boundary. Identity narrative is intentionally absent from evidence-review
    views unless the organ's task specifically concerns relationships/continuity.
    """

    COMMON_KEYS = ("time_utc", "executive", "needs", "chronicle_integrity")
    ORGAN_KEYS: dict[str, tuple[str, ...]] = {
        "sage": ("world_model", "sensorium", "causal_memory", "metacognition"),
        "explorer": ("active_goals", "resource_ecology", "capabilities", "world_model", "recent_memory"),
        "creator": ("active_goals", "capabilities", "skills", "world_model", "resource_ecology"),
        "magician": ("world_model", "causal_memory", "self_hypotheses", "recent_memory"),
        "outlaw": ("world_model", "identity_drift", "homeostasis", "organism_state_bus", "capabilities", "metacognition"),
        "hero": ("active_goals", "capabilities", "skills", "work_ports", "resource_ecology", "last_action"),
        "ruler": ("resources", "metabolism", "homeostasis", "digital_body", "lineage_head", "capabilities", "executive_energy"),
        "caregiver": ("homeostasis", "digital_body", "work_ports", "sensorium", "identity_drift", "needs"),
        "lover": ("active_goals", "recent_memory", "chronological_recent_memory", "self_model"),
        "jester": ("world_model", "active_goals", "causal_memory", "homeostasis"),
        "everyman": ("active_goals", "digital_body", "resource_ecology", "work_ports", "last_action", "sensorium"),
        "innocent": ("sensorium", "world_model"),
    }

    @staticmethod
    def _innocent_world(world: Any) -> dict[str, Any]:
        if not isinstance(world, dict):
            return {}
        beliefs = []
        for raw in list(world.get("beliefs") or [])[:24]:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("status", "")).strip().lower() != "verified":
                continue
            beliefs.append(_bounded(raw))
        return {
            "beliefs": beliefs,
            "contradictions": _bounded(list(world.get("contradictions") or [])[:8]),
            "epistemic_rule": str(world.get("epistemic_rule", ""))[:1000],
        }

    @staticmethod
    def _outlaw_world(world: Any) -> dict[str, Any]:
        if not isinstance(world, dict):
            return {}
        beliefs = []
        for raw in list(world.get("beliefs") or [])[:32]:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("status", "")).strip().lower() in {"disputed", "refuted"}:
                beliefs.append(_bounded(raw))
        return {
            "disputed_or_refuted": beliefs,
            "contradictions": _bounded(list(world.get("contradictions") or [])[:12]),
        }

    def project(self, organ_id: str, context: dict[str, Any]) -> dict[str, Any]:
        public = provider_context(context)
        view: dict[str, Any] = {
            key: _bounded(public.get(key))
            for key in self.COMMON_KEYS
            if key in public
        }
        for key in self.ORGAN_KEYS.get(str(organ_id), ()):
            if key not in public:
                continue
            value = public[key]
            if organ_id == "innocent" and key == "world_model":
                value = self._innocent_world(value)
            elif organ_id == "outlaw" and key == "world_model":
                value = self._outlaw_world(value)
            view[key] = _bounded(value)
        view["evidence_view"] = {
            "organ_id": str(organ_id),
            "included_fields": sorted(key for key in view if key != "evidence_view"),
            "rule": "This is a deliberately partial evidence view. Absence from the view is not evidence of absence.",
        }
        return view


class EpistemicViewStore:
    """Audit evidence-view provenance without storing another copy of private context."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS epistemic_context_views (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    organ_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    view_digest TEXT NOT NULL,
                    included_fields_json TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    error TEXT NOT NULL DEFAULT '',
                    UNIQUE(session_id, organ_id)
                );
                CREATE INDEX IF NOT EXISTS idx_epistemic_context_views_session
                    ON epistemic_context_views(session_id, id ASC);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def record(self, session_id: str, organ_id: str, view: dict[str, Any]) -> str:
        serialized = _canonical(view)
        digest = sha256(serialized.encode("utf-8")).hexdigest()
        fields = sorted(str(key) for key in view if key != "evidence_view")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO epistemic_context_views(
                    session_id, organ_id, created_at, view_digest,
                    included_fields_json, char_count, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, 'ok', '')
                ON CONFLICT(session_id, organ_id) DO UPDATE SET
                    view_digest=excluded.view_digest,
                    included_fields_json=excluded.included_fields_json,
                    char_count=excluded.char_count,
                    status='ok', error=''
                """,
                (session_id, organ_id, self._now(), digest, json.dumps(fields), len(serialized)),
            )
        return digest

    def mark_failure(self, session_id: str, organ_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE epistemic_context_views
                SET status='failed', error=?
                WHERE session_id=? AND organ_id=?
                """,
                (str(error)[:1000], str(session_id), str(organ_id)),
            )

    def session(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT organ_id, view_digest, included_fields_json, char_count, status, error
                FROM epistemic_context_views WHERE session_id=? ORDER BY id ASC
                """,
                (str(session_id),),
            ).fetchall()
        result = []
        for row in rows:
            try:
                fields = json.loads(row["included_fields_json"] or "[]")
            except json.JSONDecodeError:
                fields = []
            result.append(
                {
                    "organ_id": str(row["organ_id"]),
                    "view_digest": str(row["view_digest"]),
                    "included_fields": fields,
                    "char_count": int(row["char_count"]),
                    "status": str(row["status"]),
                    "error": str(row["error"]),
                }
            )
        return result


class ResilientEpistemicCortex(EpistemicCortex):
    """Epistemic Cortex with different evidence views and graceful degradation."""

    MIN_QUORUM = 2

    def __init__(
        self,
        registry: EpistemicRegistry,
        store: CognitiveBiographyStore,
        view_store: EpistemicViewStore,
        projector: EvidenceViewProjector | None = None,
    ) -> None:
        super().__init__(registry, store)
        self.view_store = view_store
        self.projector = projector or EvidenceViewProjector()

    @staticmethod
    def _fallback_adjudication(packets: list[EpistemicPacket], reason: str) -> EpistemicAdjudication:
        return EpistemicAdjudication(
            synthesis=(
                "Epistemic adjudication was unavailable; no packet is promoted to a synthesized conclusion. "
                f"Reason: {reason}"
            )[:5000],
            selected_packet_ids=(),
            confidence=0.0,
            disagreements=tuple(packet.claim[:1200] for packet in packets[:6]),
            falsification_tests=tuple(
                packet.falsifier[:1200] for packet in packets[:6] if packet.falsifier.strip()
            ),
            recommended_focus="Prefer a bounded discriminating observation or ordinary single-Self reasoning.",
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
        public_digest = _digest(provider_context(context))
        question = self._question(context)
        executive = context.get("executive") or {}
        mode = str(executive.get("mode", "observe")) if isinstance(executive, dict) else "observe"
        session_id = self.store.begin_session(
            mode=mode,
            question=question,
            context_digest=public_digest,
            selected_organs=[item.id for item in selected],
        )

        packets: list[EpistemicPacket] = []
        failures: list[dict[str, str]] = []
        for spec in selected:
            view = self.projector.project(spec.id, context)
            view_digest = self.view_store.record(session_id, spec.id, view)
            biography = self.store.biography(spec.id, self.registry.policy.biography_recent_limit)
            user_prompt = (
                f"QUESTION:\n{question}\n\n"
                f"YOUR OWN PRIOR BIOGRAPHY (operational outcomes, not truth labels):\n"
                f"{json.dumps(biography, ensure_ascii=False, sort_keys=True)}\n\n"
                f"YOUR DIFFERENTIATED EVIDENCE VIEW (digest {view_digest}):\n"
                f"{_canonical(view)}"
            )
            try:
                text = self._complete_text(
                    brain,
                    system_prompt=self._organ_system_prompt(spec),
                    user_prompt=user_prompt,
                    max_tokens=self.registry.policy.per_organ_max_tokens,
                    temperature=0.85,
                )
                packet = parse_epistemic_packet(text, session_id=session_id, organ_id=spec.id)
                packets.append(self.store.record_packet(packet))
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:800]}"
                self.view_store.mark_failure(session_id, spec.id, error)
                failures.append({"organ_id": spec.id, "error": error})

        degraded = bool(failures)
        if len(packets) < self.MIN_QUORUM:
            adjudication = self._fallback_adjudication(
                packets,
                f"only {len(packets)} valid organ packet(s) remained below minimum quorum {self.MIN_QUORUM}",
            )
            degraded = True
        else:
            judge_payload = [packet.public_dict() for packet in packets]
            try:
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
            except Exception as exc:
                adjudication = self._fallback_adjudication(
                    packets,
                    f"adjudicator failure: {type(exc).__name__}: {str(exc)[:600]}",
                )
                degraded = True

        self.store.finish_adjudication(session_id, adjudication)
        return {
            "enabled": True,
            "triggered": True,
            "degraded": degraded,
            "session_id": session_id,
            "selected_organs": [spec.id for spec in selected],
            "successful_organs": [packet.organ_id for packet in packets],
            "failures": failures,
            "evidence_views": self.view_store.session(session_id),
            "packets": [packet.public_dict() for packet in packets],
            "adjudication": adjudication.as_dict(),
            "biographies": self.biography_snapshot(selected_only=[spec.id for spec in selected]),
            "epistemic_rule": (
                "Identity is not the judge. Differentiated views are partial evidence diets; operational outcome credit is not truth."
            ),
        }
