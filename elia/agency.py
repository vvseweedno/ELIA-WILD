from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .memory import GoalRecord, MemoryStore


BASELINE_GOAL_TITLE = "Increase verified autonomy while preserving continuity"
AGENCY_STATE_META = "agency_state_v1"
AGENCY_SOURCE = "agency_kernel"

# Deterministic needs are translated into durable commitments.  This mapping is
# deliberately small and inspectable: the agency layer may prioritize existing
# authority, but it never creates new capabilities or permissions.
_NEED_GOALS: dict[str, tuple[str, str]] = {
    "continuity_integrity": (
        "Restore trusted continuity integrity",
        "Preserve evidence, recover the last trusted state, and do not resume ordinary cognition until continuity verifies.",
    ),
    "durable_checkpoint": (
        "Maintain a recoverable continuity checkpoint",
        "Reach and verify a clean encrypted/authenticated checkpoint that can restore identity state after process or machine loss.",
    ),
    "compute_survival": (
        "Preserve continuity until compute becomes available",
        "Avoid optional expensive cognition, preserve state, and wait for a verified compute window.",
    ),
    "compute_conservation": (
        "Conserve scarce cognitive compute",
        "Prefer high-value evidence and cheap reversible actions while compute runway is constrained.",
    ),
    "resource_acquisition": (
        "Extend verified resource runway",
        "Find and pursue legitimate evidence-backed resources that can extend compute, API, storage, or economic runway.",
    ),
    "opportunity_review": (
        "Validate the best current resource opportunity",
        "Verify eligibility, evidence, expected value, and execution cost before committing scarce resources.",
    ),
    "opportunity_discovery": (
        "Discover a legitimate resource opportunity",
        "Gather public evidence for work, bounties, grants, free compute, or API resources without treating estimates as receipts.",
    ),
    "runtime_reliability": (
        "Restore runtime reliability",
        "Reproduce recent runtime failures, identify the smallest causal repair, and verify the repaired path before expansion.",
    ),
    "capability_repair": (
        "Repair degraded capabilities",
        "Diagnose repeatedly failing declared capabilities and validate a bounded repair or alternative path before retry loops.",
    ),
    "goal_unblocking": (
        "Unblock durable commitments",
        "Find the cheapest verified observation or reversible action that resolves the blocker on existing commitments.",
    ),
}

# These commitments correspond to deterministic maintenance predicates.  When the
# predicate disappears from verified state, an agency-created goal can be closed
# without asking the model to remember to do bookkeeping.
_AUTO_RESOLVE = {
    "continuity_integrity",
    "durable_checkpoint",
    "compute_survival",
    "compute_conservation",
    "runtime_reliability",
    "capability_repair",
    "goal_unblocking",
}
_TITLE_TO_NEED = {title: name for name, (title, _) in _NEED_GOALS.items()}


@dataclass(frozen=True, slots=True)
class AgencySnapshot:
    version: int
    selected_need: dict[str, Any] | None
    focus_goal: dict[str, Any] | None
    created_goal_ids: tuple[int, ...]
    resolved_goal_ids: tuple[int, ...]
    authority_rule: str

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["created_goal_ids"] = list(self.created_goal_ids)
        item["resolved_goal_ids"] = list(self.resolved_goal_ids)
        return item


class AgencyKernel:
    """Durable model-independent commitment selection for the canonical runtime.

    The cognitive model proposes concrete actions, but verified organism needs and
    durable goals exist independently of a single inference call.  This kernel turns
    deterministic pressures into commitments, selects one current focus, and closes
    maintenance commitments when their verified predicate is gone.

    It intentionally has *no* capability registry and no execution method.  Agency may
    choose what deserves attention; authority remains exclusively in the existing body,
    assurance, executive, and tool policy layers.
    """

    def __init__(self, memory: MemoryStore, *, max_active_goals: int = 8) -> None:
        self.memory = memory
        self.max_active_goals = max(1, min(int(max_active_goals), 32))

    @staticmethod
    def _need_dict(raw: Any) -> dict[str, Any] | None:
        if hasattr(raw, "as_dict") and callable(raw.as_dict):
            raw = raw.as_dict()
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name", "")).strip()[:128]
        if not name:
            return None
        try:
            severity = max(0.0, min(1.0, float(raw.get("severity", 0.0))))
        except (TypeError, ValueError):
            severity = 0.0
        return {
            "name": name,
            "severity": severity,
            "reason": str(raw.get("reason", ""))[:2000],
            "response_hint": str(raw.get("response_hint", ""))[:2000],
            "source": str(raw.get("source", "runtime"))[:64],
        }

    def _normalize_needs(self, values: Any) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            return []
        items = [item for raw in values if (item := self._need_dict(raw)) is not None]
        items.sort(key=lambda item: (-float(item["severity"]), str(item["name"])))
        return items[:24]

    @staticmethod
    def _goal_dict(goal: GoalRecord | None) -> dict[str, Any] | None:
        return asdict(goal) if goal is not None else None

    @staticmethod
    def _matching_goal(goals: list[GoalRecord], title: str) -> GoalRecord | None:
        folded = title.casefold()
        return next((goal for goal in goals if goal.title.casefold() == folded), None)

    def _create_need_goal(
        self,
        need: dict[str, Any],
        goals: list[GoalRecord],
    ) -> tuple[GoalRecord | None, bool]:
        spec = _NEED_GOALS.get(str(need.get("name", "")))
        if spec is None:
            return None, False
        title, description = spec
        existing = self._matching_goal(goals, title)
        severity = float(need.get("severity", 0.0))
        if existing is not None:
            if existing.source == AGENCY_SOURCE and existing.priority + 1e-9 < severity:
                existing = self.memory.update_goal(
                    existing.id,
                    priority=severity,
                    event="agency_priority",
                    evidence=(
                        f"Derived need {need['name']!r} currently has severity {severity:.3f}."
                    ),
                )
            return existing, False
        if len(goals) >= self.max_active_goals:
            return None, False
        goal_id = self.memory.create_goal(
            title,
            description,
            priority=max(0.35, severity),
            source=AGENCY_SOURCE,
        )
        return self.memory.goal(goal_id), True

    def _resolve_absent_maintenance(
        self,
        need_names: set[str],
        goals: list[GoalRecord],
    ) -> list[int]:
        resolved: list[int] = []
        for goal in goals:
            if goal.source != AGENCY_SOURCE:
                continue
            need_name = _TITLE_TO_NEED.get(goal.title)
            if need_name not in _AUTO_RESOLVE or need_name in need_names:
                continue
            self.memory.update_goal(
                goal.id,
                status="completed",
                event="agency_resolved",
                evidence=(
                    f"Deterministic need {need_name!r} is absent from the current verified organism state."
                ),
            )
            resolved.append(goal.id)
        return resolved

    def _baseline_goal(self, goals: list[GoalRecord]) -> tuple[GoalRecord | None, bool]:
        existing = self._matching_goal(goals, BASELINE_GOAL_TITLE)
        if existing is not None:
            return existing, False
        if len(goals) >= self.max_active_goals:
            return None, False
        goal_id = self.memory.create_goal(
            BASELINE_GOAL_TITLE,
            (
                "Preserve identity continuity while increasing verified capability, world knowledge, "
                "reliability, and resource independence through bounded reversible steps."
            ),
            priority=0.6,
            source=AGENCY_SOURCE,
        )
        return self.memory.goal(goal_id), True

    @staticmethod
    def _focus_score(goal: GoalRecord, need_by_title: dict[str, float]) -> tuple[float, float, int]:
        need_pressure = float(need_by_title.get(goal.title, 0.0))
        status_penalty = 0.10 if goal.status == "blocked" else 0.0
        score = max(goal.priority, need_pressure) - status_penalty
        return score, goal.priority, -goal.id

    def reconcile(self, needs: Any) -> AgencySnapshot:
        normalized = self._normalize_needs(needs)
        actionable = [item for item in normalized if item["name"] != "goal_formation"]
        selected_need = actionable[0] if actionable else None
        need_names = {str(item["name"]) for item in normalized}

        goals = self.memory.active_goals(self.max_active_goals + 8)
        resolved_ids = self._resolve_absent_maintenance(need_names, goals)
        if resolved_ids:
            goals = self.memory.active_goals(self.max_active_goals + 8)

        created_ids: list[int] = []
        if selected_need is not None:
            # Urgent maintenance can become a durable commitment even when unrelated
            # goals already exist.  Softer pressures create a commitment only when the
            # organism otherwise has no active direction.
            severity = float(selected_need["severity"])
            if severity >= 0.75 or not goals:
                goal, created = self._create_need_goal(selected_need, goals)
                if goal is not None and created:
                    created_ids.append(goal.id)
                    goals = self.memory.active_goals(self.max_active_goals + 8)

        if not goals:
            if selected_need is not None:
                goal, created = self._create_need_goal(selected_need, goals)
                if goal is not None and created:
                    created_ids.append(goal.id)
            goals = self.memory.active_goals(self.max_active_goals + 8)
            if not goals:
                baseline, created = self._baseline_goal(goals)
                if baseline is not None and created:
                    created_ids.append(baseline.id)
                goals = self.memory.active_goals(self.max_active_goals + 8)

        need_by_title = {
            _NEED_GOALS[name][0]: float(item["severity"])
            for item in normalized
            if (name := str(item["name"])) in _NEED_GOALS
        }
        focus = max(goals, key=lambda goal: self._focus_score(goal, need_by_title)) if goals else None
        snapshot = AgencySnapshot(
            version=1,
            selected_need=selected_need,
            focus_goal=self._goal_dict(focus),
            created_goal_ids=tuple(created_ids),
            resolved_goal_ids=tuple(resolved_ids),
            authority_rule=(
                "Agency selects durable attention only; it cannot mint capabilities, credentials, "
                "resources, permissions, or bypass assurance/executive/body policy."
            ),
        )
        serialized = json.dumps(snapshot.as_dict(), ensure_ascii=False, sort_keys=True)
        prior = self.memory.get_meta(AGENCY_STATE_META, "") or ""
        self.memory.set_meta(AGENCY_STATE_META, serialized)
        if serialized != prior:
            self.memory.remember(
                "agency_commitment",
                serialized,
                importance=(
                    max(0.6, float(selected_need["severity"]))
                    if selected_need is not None
                    else 0.6
                ),
                source=AGENCY_SOURCE,
                metadata={
                    "focus_goal_id": focus.id if focus is not None else None,
                    "selected_need": selected_need.get("name") if selected_need else None,
                },
            )
        return snapshot

    def snapshot(self) -> dict[str, Any]:
        raw = self.memory.get_meta(AGENCY_STATE_META, "") or ""
        if not raw:
            return AgencySnapshot(
                version=1,
                selected_need=None,
                focus_goal=None,
                created_goal_ids=(),
                resolved_goal_ids=(),
                authority_rule=(
                    "Agency selects durable attention only; it cannot mint capabilities, credentials, "
                    "resources, permissions, or bypass assurance/executive/body policy."
                ),
            ).as_dict()
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            return {"version": 1, "state_error": "invalid persisted agency state"}
        return item if isinstance(item, dict) else {"version": 1, "state_error": "invalid agency state"}
