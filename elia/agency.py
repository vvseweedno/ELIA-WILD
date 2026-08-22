from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from types import MappingProxyType
from typing import Any, Literal, Mapping

from .memory import GoalRecord, MemoryStore


BASELINE_GOAL_TITLE = "Increase verified autonomy while preserving continuity"
AGENCY_STATE_META = "agency_state_v1"
AGENCY_SOURCE = "agency_kernel"

NeedCategory = Literal[
    "continuity",
    "compute",
    "resource",
    "maintenance",
    "epistemic",
    "mission",
]


@dataclass(frozen=True, slots=True)
class NeedSpec:
    """Canonical contract for every deterministic need emitted by the organism.

    A need name is an internal typed protocol, not free-form model text. Keeping its
    goal translation, Executive class and wake deadline together prevents one layer
    from silently spelling or classifying the same pressure differently.
    """

    name: str
    category: NeedCategory
    goal_title: str
    goal_description: str
    wake_cap_seconds: float
    auto_resolve: bool = True
    hard_stop: bool = False


def _need_spec(
    name: str,
    category: NeedCategory,
    title: str,
    description: str,
    wake_cap_seconds: float,
    *,
    auto_resolve: bool = True,
    hard_stop: bool = False,
) -> NeedSpec:
    return NeedSpec(
        name=name,
        category=category,
        goal_title=title,
        goal_description=description,
        wake_cap_seconds=wake_cap_seconds,
        auto_resolve=auto_resolve,
        hard_stop=hard_stop,
    )


# This registry is the single namespace consumed by Agency, Executive and
# Homeostasis. It is intentionally exhaustive for derive_needs() and all current
# homeostatic signal producers.
_NEED_REGISTRY: dict[str, NeedSpec] = {
    "continuity_integrity": _need_spec(
        "continuity_integrity",
        "continuity",
        "Restore trusted continuity integrity",
        "Preserve evidence, recover the last trusted state, and do not resume ordinary cognition until continuity verifies.",
        300.0,
        hard_stop=True,
    ),
    "identity_drift": _need_spec(
        "identity_drift",
        "continuity",
        "Resolve a critical structural identity invariant failure",
        "Preserve evidence and restore the last state satisfying the protected structural identity invariants before ordinary cognition resumes.",
        300.0,
        hard_stop=True,
    ),
    "durable_checkpoint": _need_spec(
        "durable_checkpoint",
        "maintenance",
        "Maintain a recoverable continuity checkpoint",
        "Reach and verify a clean encrypted/authenticated checkpoint that can restore identity state after process or machine loss.",
        1800.0,
    ),
    "compute_survival": _need_spec(
        "compute_survival",
        "compute",
        "Preserve continuity until compute becomes available",
        "Avoid optional expensive cognition, preserve state, and wait for a verified compute window.",
        21600.0,
    ),
    "compute_conservation": _need_spec(
        "compute_conservation",
        "compute",
        "Conserve scarce cognitive compute",
        "Prefer high-value evidence and cheap reversible actions while compute runway is constrained.",
        21600.0,
    ),
    "resource_acquisition": _need_spec(
        "resource_acquisition",
        "resource",
        "Extend verified resource runway",
        "Find and pursue legitimate evidence-backed resources that can extend compute, API, storage, or economic runway.",
        21600.0,
        auto_resolve=False,
    ),
    "resource_execution": _need_spec(
        "resource_execution",
        "resource",
        "Advance the best exact typed resource candidate",
        "Qualify or progress the best evidence-backed candidate for the current exact resource key without treating expected reward as received value.",
        3600.0,
    ),
    "resource_discovery": _need_spec(
        "resource_discovery",
        "resource",
        "Find a candidate for the exact constrained resource",
        "Search for legitimate opportunities targeting the exact constrained asset/unit pair without substituting unrelated units.",
        7200.0,
    ),
    "work_execution": _need_spec(
        "work_execution",
        "resource",
        "Advance one unfinished verified work item",
        "Continue one evidence-backed resource work item while preserving the distinction between artifact, submission, acceptance, and realized payment.",
        3600.0,
    ),
    "opportunity_review": _need_spec(
        "opportunity_review",
        "resource",
        "Validate the best current resource opportunity",
        "Verify eligibility, evidence, expected value, and execution cost before committing scarce resources.",
        21600.0,
        auto_resolve=False,
    ),
    "opportunity_discovery": _need_spec(
        "opportunity_discovery",
        "resource",
        "Discover a legitimate resource opportunity",
        "Gather public evidence for work, bounties, grants, free compute, or API resources without treating estimates as receipts.",
        21600.0,
        auto_resolve=False,
    ),
    "resource_runway": _need_spec(
        "resource_runway",
        "resource",
        "Restore verified essential resource runway",
        "Reduce a verified obligation or obtain an exactly typed verified resource without mixing units or treating estimates as receipts.",
        3600.0,
    ),
    "uncovered_essential_obligation": _need_spec(
        "uncovered_essential_obligation",
        "resource",
        "Cover the earliest verified essential obligation",
        "Address the earliest cumulative cash-flow shortfall through an authorized resource, reduction, replacement, or truthful retirement of the obligation.",
        1800.0,
    ),
    "body_readiness": _need_spec(
        "body_readiness",
        "maintenance",
        "Establish evidence-backed body readiness",
        "Diagnose unavailable actuation and prepare a bounded deployment plan without inventing authority or credentials.",
        21600.0,
    ),
    "runtime_reliability": _need_spec(
        "runtime_reliability",
        "maintenance",
        "Restore runtime reliability",
        "Reproduce recent runtime failures, identify the smallest causal repair, and verify the repaired path before expansion.",
        3600.0,
    ),
    "capability_repair": _need_spec(
        "capability_repair",
        "maintenance",
        "Repair degraded capabilities",
        "Diagnose repeatedly failing declared capabilities and validate a bounded repair or alternative path before retry loops.",
        3600.0,
    ),
    "storage_survival": _need_spec(
        "storage_survival",
        "maintenance",
        "Preserve state under critical storage pressure",
        "Avoid large writes and prepare an authorized, evidence-preserving storage recovery action.",
        900.0,
    ),
    "storage_conservation": _need_spec(
        "storage_conservation",
        "maintenance",
        "Conserve persistent storage",
        "Prefer compact evidence and a reviewed cleanup proposal while preserving recovery material.",
        7200.0,
    ),
    "state_reconciliation": _need_spec(
        "state_reconciliation",
        "maintenance",
        "Reconcile incomplete organism transactions",
        "Recover or roll back interrupted transitions before optional activity.",
        1800.0,
    ),
    "sensorium_degradation": _need_spec(
        "sensorium_degradation",
        "maintenance",
        "Restore reliable observation",
        "Diagnose failing sensors or choose a healthy alternative before relying on repeated observations.",
        3600.0,
    ),
    "epistemic_conflict": _need_spec(
        "epistemic_conflict",
        "epistemic",
        "Resolve a material epistemic conflict",
        "Seek a bounded discriminating observation while preserving contradictory claims and uncertainty.",
        7200.0,
    ),
    "goal_unblocking": _need_spec(
        "goal_unblocking",
        "mission",
        "Unblock durable commitments",
        "Find the cheapest verified observation or reversible action that resolves the blocker on existing commitments.",
        7200.0,
    ),
    "goal_formation": _need_spec(
        "goal_formation",
        "mission",
        BASELINE_GOAL_TITLE,
        "Preserve identity continuity while increasing verified capability, world knowledge, reliability, and resource independence through bounded reversible steps.",
        21600.0,
        auto_resolve=False,
    ),
}

NEED_REGISTRY: Mapping[str, NeedSpec] = MappingProxyType(_NEED_REGISTRY)

for _registry_name, _registry_spec in NEED_REGISTRY.items():
    if _registry_name != _registry_spec.name:
        raise RuntimeError(f"need registry key/name mismatch: {_registry_name!r}")
    if not _registry_spec.goal_title or not _registry_spec.goal_description:
        raise RuntimeError(f"need registry goal contract is incomplete: {_registry_name!r}")
    if (
        not math.isfinite(_registry_spec.wake_cap_seconds)
        or _registry_spec.wake_cap_seconds <= 0
    ):
        raise RuntimeError(f"need registry wake cap is invalid: {_registry_name!r}")

_TITLE_TO_NEED = {spec.goal_title: name for name, spec in NEED_REGISTRY.items()}
_WORK_STATUS_PRIORITY = {
    # Accepted work has unresolved resource realization; submitted work has unresolved
    # external outcome; staged work is ready to leave the local trust boundary; planned
    # work still needs a deliverable. Preserve this causal order between wake sessions.
    "accepted": 4,
    "submitted": 3,
    "staged": 2,
    "planned": 1,
}

# All statuses earn priority at the same rate. Using a faster aging rate for already
# advanced work would make its lead grow forever and defeat the fairness guarantee.
_WORK_AGING_QUANTUM_SECONDS = 6 * 3600.0
_MAX_WORK_CLOCK_SKEW_SECONDS = 300.0

# The model may always request an earlier wake, but it may not postpone a verified
# commitment beyond these deterministic deadlines. The external heartbeat is currently
# hourly, so sub-hour deadlines mean "launch on the next available heartbeat" rather
# than pretending the transport can schedule more frequently than its platform permits.
_WORK_WAKE_CAP_SECONDS: dict[str, float] = {
    "accepted": 3600.0,
    "submitted": 3600.0,
    "staged": 7200.0,
    "planned": 21600.0,
}


@dataclass(frozen=True, slots=True)
class AgencySnapshot:
    version: int
    selected_need: dict[str, Any] | None
    active_needs: tuple[dict[str, Any], ...]
    focus_goal: dict[str, Any] | None
    continuation_work_item: dict[str, Any] | None
    created_goal_ids: tuple[int, ...]
    resolved_goal_ids: tuple[int, ...]
    authority_rule: str

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["active_needs"] = [dict(need) for need in self.active_needs]
        item["created_goal_ids"] = list(self.created_goal_ids)
        item["resolved_goal_ids"] = list(self.resolved_goal_ids)
        return item


class AgencyKernel:
    """Durable model-independent commitment selection for the canonical runtime.

    The cognitive model proposes concrete actions, but verified organism needs,
    durable goals and unfinished work exist independently of a single inference call.
    This kernel turns deterministic pressures into commitments, selects one current
    goal, preserves a cursor to unfinished work, closes maintenance commitments when
    their verified predicate is gone, and bounds how long those obligations may sleep.

    It intentionally has *no* capability registry and no execution method. Agency may
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
        if not name or name not in NEED_REGISTRY:
            return None
        try:
            severity = float(raw.get("severity", 0.0))
        except (TypeError, ValueError):
            severity = 0.0
        if not math.isfinite(severity):
            severity = 0.0
        severity = max(0.0, min(1.0, severity))
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
    def _work_dict(raw: Any) -> dict[str, Any] | None:
        if hasattr(raw, "as_dict") and callable(raw.as_dict):
            raw = raw.as_dict()
        if not isinstance(raw, dict):
            return None
        try:
            work_id = int(raw["id"])
            opportunity_id = int(raw["opportunity_id"])
            estimated_gpu_hours = float(raw.get("estimated_gpu_hours", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(estimated_gpu_hours) or estimated_gpu_hours < 0.0:
            return None
        status = str(raw.get("status", "")).strip().lower()
        if work_id < 1 or opportunity_id < 1 or status not in _WORK_STATUS_PRIORITY:
            return None
        updated_at = str(raw.get("updated_at", ""))[:64]
        try:
            updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        item: dict[str, Any] = {
            "id": work_id,
            "opportunity_id": opportunity_id,
            "status": status,
            "objective": str(raw.get("objective", ""))[:2000],
            "estimated_gpu_hours": estimated_gpu_hours,
            "updated_at": updated.astimezone(timezone.utc).isoformat(),
        }
        for key in ("artifact_path", "submission_observation_id", "resource_event_id"):
            if raw.get(key) is not None:
                item[key] = raw.get(key)
        return item

    def _continuation_work(
        self,
        values: Any,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(values, list):
            return None
        items = [item for raw in values if (item := self._work_dict(raw)) is not None]
        if not items:
            return None
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        eligible: list[dict[str, Any]] = []
        for item in items:
            updated = datetime.fromisoformat(str(item["updated_at"]))
            future_skew = (updated - now).total_seconds()
            if future_skew > _MAX_WORK_CLOCK_SKEW_SECONDS:
                # A corrupt/hostile timestamp must not suppress aging indefinitely.
                continue
            effective_updated = min(updated, now)
            if future_skew > 0.0:
                item["clock_skew_clamped_seconds"] = future_skew
            age_seconds = max(0.0, (now - effective_updated).total_seconds())
            quantum = _WORK_AGING_QUANTUM_SECONDS
            boost = int(age_seconds // quantum)
            item["aging_priority_boost"] = boost
            item["effective_priority"] = _WORK_STATUS_PRIORITY[str(item["status"])] + boost
            item["next_aging_at"] = (
                effective_updated + timedelta(seconds=(boost + 1) * quantum)
            ).isoformat()
            eligible.append(item)

        eligible.sort(
            key=lambda item: (
                -int(item["effective_priority"]),
                -_WORK_STATUS_PRIORITY[str(item["status"])],
                str(item.get("updated_at", "")),
                int(item["id"]),
            )
        )
        return eligible[0] if eligible else None

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
        spec = NEED_REGISTRY.get(str(need.get("name", "")))
        if spec is None:
            return None, False
        title, description = spec.goal_title, spec.goal_description
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
        # Hard-stop continuity needs get one durable emergency slot rather than being
        # hidden behind a full set of ordinary goals. Existing goals are preserved and
        # this only commits attention; it grants no execution authority.
        if len(goals) >= self.max_active_goals and not spec.hard_stop:
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
            spec = NEED_REGISTRY.get(str(need_name))
            if spec is None or not spec.auto_resolve or need_name in need_names:
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

    def reconcile(
        self,
        needs: Any,
        *,
        active_work: Any = None,
        now: datetime | None = None,
    ) -> AgencySnapshot:
        normalized = self._normalize_needs(needs)
        actionable = [item for item in normalized if item["name"] != "goal_formation"]
        selected_need = actionable[0] if actionable else None
        need_names = {str(item["name"]) for item in normalized}
        continuation_work = self._continuation_work(active_work, now=now)

        goals = self.memory.active_goals(self.max_active_goals + 8)
        resolved_ids = self._resolve_absent_maintenance(need_names, goals)
        if resolved_ids:
            goals = self.memory.active_goals(self.max_active_goals + 8)

        created_ids: list[int] = []
        if selected_need is not None:
            # Urgent maintenance can become a durable commitment even when unrelated
            # goals already exist. Softer pressures create a commitment only when the
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
            NEED_REGISTRY[name].goal_title: float(item["severity"])
            for item in normalized
            if (name := str(item["name"])) in NEED_REGISTRY
        }
        focus = max(goals, key=lambda goal: self._focus_score(goal, need_by_title)) if goals else None
        focus_dict = self._goal_dict(focus)
        if selected_need is not None:
            selected_name = str(selected_need["name"])
            selected_spec = NEED_REGISTRY[selected_name]
            selected_goal = self._matching_goal(goals, selected_spec.goal_title)
            severity = float(selected_need["severity"])
            if selected_spec.hard_stop or severity >= 0.75:
                if selected_goal is not None:
                    focus = selected_goal
                    focus_dict = self._goal_dict(selected_goal)
                else:
                    # At the normal goal cap, an urgent non-hard-stop pressure remains
                    # an explicit synthetic attention focus instead of being silently
                    # displaced by an unrelated durable goal.
                    focus = None
                    focus_dict = {
                        "id": None,
                        "title": selected_spec.goal_title,
                        "description": selected_spec.goal_description,
                        "priority": severity,
                        "status": "derived_need",
                        "source": AGENCY_SOURCE,
                        "parent_id": None,
                        "need_name": selected_name,
                        "durable": False,
                    }
        snapshot = AgencySnapshot(
            version=2,
            selected_need=selected_need,
            active_needs=tuple(actionable),
            focus_goal=focus_dict,
            continuation_work_item=continuation_work,
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
                    "focus_goal_id": (
                        focus_dict.get("id") if isinstance(focus_dict, dict) else None
                    ),
                    "continuation_work_item_id": (
                        continuation_work.get("id") if continuation_work else None
                    ),
                    "selected_need": selected_need.get("name") if selected_need else None,
                },
            )
        return snapshot

    def snapshot(self) -> dict[str, Any]:
        raw = self.memory.get_meta(AGENCY_STATE_META, "") or ""
        if not raw:
            return AgencySnapshot(
                version=2,
                selected_need=None,
                active_needs=(),
                focus_goal=None,
                continuation_work_item=None,
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

    def wake_policy(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the deterministic maximum sleep permitted by current commitments."""

        state = snapshot if isinstance(snapshot, dict) else self.snapshot()
        candidates: list[tuple[float, str]] = []

        selected = state.get("selected_need")
        active_needs = state.get("active_needs")
        obligation_names: set[str] = set()
        if isinstance(active_needs, (list, tuple)):
            for need in active_needs:
                if isinstance(need, dict):
                    name = str(need.get("name", ""))
                    if name in NEED_REGISTRY:
                        obligation_names.add(name)
        # Backward compatibility for durable v1 snapshots and defensive inclusion if
        # a partially migrated producer omitted the selected item from active_needs.
        if isinstance(selected, dict):
            name = str(selected.get("name", ""))
            if name in NEED_REGISTRY:
                obligation_names.add(name)
        for name in sorted(obligation_names):
            spec = NEED_REGISTRY.get(name)
            if spec is not None:
                candidates.append((spec.wake_cap_seconds, f"need:{name}"))

        work = state.get("continuation_work_item")
        if isinstance(work, dict):
            status = str(work.get("status", "")).strip().lower()
            cap = _WORK_WAKE_CAP_SECONDS.get(status)
            if cap is not None:
                candidates.append((cap, f"work:{status}"))

        if not candidates:
            return {
                "max_sleep_seconds": None,
                "reason": "no deterministic agency wake deadline",
                "selected_need": (
                    str(selected.get("name", "")) if isinstance(selected, dict) else None
                ),
                "continuation_work_item_id": (
                    work.get("id") if isinstance(work, dict) else None
                ),
                "active_need_names": sorted(obligation_names),
            }

        cap, reason = min(candidates, key=lambda item: (item[0], item[1]))
        return {
            "max_sleep_seconds": cap,
            "reason": reason,
            "selected_need": (
                str(selected.get("name", "")) if isinstance(selected, dict) else None
            ),
            "continuation_work_item_id": (
                work.get("id") if isinstance(work, dict) else None
            ),
            "active_need_names": sorted(obligation_names),
        }
