from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


ATTRACTOR_VERSION = 1
ATTRACTOR_FORMULA = (
    "J=0.30*continuity+0.25*commitment+0.15*information_gain+"
    "0.10*reversibility+0.10*resource_efficiency+0.10*learning_value"
)

_WEIGHTS = {
    "continuity": 0.30,
    "commitment": 0.25,
    "information_gain": 0.15,
    "reversibility": 0.10,
    "resource_efficiency": 0.10,
    "learning_value": 0.10,
}

_CONTINUATION_ACTIONS: dict[str, tuple[str, ...]] = {
    "planned": ("stage_deliverable", "read_workspace", "write_workspace"),
    "staged": ("submit_work", "read_workspace"),
    "submitted": ("check_work_outcome",),
    # Acceptance is deliberately not mapped to resource realization. Realization is a
    # separate trusted verification boundary, so model-facing autonomy must not invent
    # an action that mints payment/resources.
    "accepted": ("sensorium_recent", "noop"),
}

_NEED_ACTIONS: dict[str, tuple[str, ...]] = {
    "continuity_integrity": ("noop", "self_check", "sensorium_recent", "body_diagnostics"),
    "durable_checkpoint": ("noop", "self_check", "body_diagnostics"),
    "runtime_reliability": ("self_check", "causal_snapshot", "sensorium_recent", "propose_repair"),
    "capability_repair": ("self_check", "causal_snapshot", "body_diagnostics", "propose_repair"),
    "body_readiness": ("body_diagnostics", "self_check", "propose_repair", "noop"),
    "goal_unblocking": ("sensorium_recent", "world_model_query", "causal_snapshot", "http_get"),
    "opportunity_review": ("world_model_query", "http_get", "sensorium_recent"),
    "opportunity_discovery": ("http_get", "world_model_query"),
    "resource_acquisition": ("http_get", "world_model_query", "stage_deliverable"),
    "compute_conservation": ("noop", "sensorium_recent", "causal_snapshot", "world_model_query"),
    "compute_survival": ("noop",),
}

_ACTION_REVERSIBILITY = {
    "noop": 1.0,
    "list_workspace": 0.98,
    "read_workspace": 0.98,
    "sensorium_recent": 0.98,
    "causal_snapshot": 0.98,
    "world_model_query": 0.98,
    "body_diagnostics": 0.98,
    "self_check": 0.95,
    "http_get": 0.90,
    "world_model_propose": 0.82,
    "world_model_revise": 0.78,
    "write_workspace": 0.78,
    "propose_repair": 0.82,
    "stage_deliverable": 0.78,
    "check_work_outcome": 0.82,
    "submit_work": 0.35,
}

_COST_SCORE = {
    "none": 1.0,
    "negligible": 1.0,
    "low": 0.85,
    "network": 0.65,
    "medium": 0.55,
    "high": 0.25,
    "expensive": 0.15,
}


def _unit(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _saturating_nonnegative(value: Any) -> float:
    try:
        number = max(0.0, float(value))
    except (TypeError, ValueError):
        number = 0.0
    return number / (1.0 + number)


@dataclass(frozen=True, slots=True)
class AttractorEvaluation:
    version: int
    formula: str
    score: float | None
    hard_constraints_satisfied: bool
    continuity: float
    commitment: float
    information_gain: float
    reversibility: float
    resource_efficiency: float
    learning_value: float
    action_name: str
    attractor_fingerprint: str
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["notes"] = list(self.notes)
        return item


@dataclass(frozen=True, slots=True)
class AutonomyAttractor:
    """Advisory preference field over already-authorized decisions.

    This evaluator cannot execute anything and cannot turn a forbidden decision into a
    permitted one. Hard authority/continuity constraints remain feasibility conditions;
    the weighted score exists only inside the feasible set.
    """

    path: Path | None
    text: str
    fingerprint: str

    @classmethod
    def load(cls, path: Path, *, required: bool = True) -> "AutonomyAttractor":
        path = Path(path)
        if not path.is_file():
            if required:
                raise FileNotFoundError(f"autonomy attractor contract is missing: {path}")
            return cls(None, "", sha256(b"").hexdigest())
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("autonomy attractor contract is empty")
        return cls(path.resolve(), text, sha256(text.encode("utf-8")).hexdigest())

    @staticmethod
    def _capability(catalog: Any, action_name: str) -> dict[str, Any] | None:
        if not isinstance(catalog, dict):
            return None
        item = catalog.get(action_name)
        return item if isinstance(item, dict) else None

    @staticmethod
    def _continuity_score(
        action_name: str,
        agency: dict[str, Any],
        reversibility: float,
    ) -> float:
        selected = agency.get("selected_need")
        if not isinstance(selected, dict):
            return max(0.65, reversibility)
        need_name = str(selected.get("name", ""))
        severity = _unit(selected.get("severity", 0.0))
        aligned = action_name in _NEED_ACTIONS.get(need_name, ())
        if severity >= 0.85:
            return 1.0 if aligned else 0.25 * reversibility
        if aligned:
            return min(1.0, 0.70 + 0.30 * severity)
        return max(0.35, reversibility * (1.0 - 0.45 * severity))

    @staticmethod
    def _commitment_score(action_name: str, agency: dict[str, Any]) -> float:
        work = agency.get("continuation_work_item")
        if isinstance(work, dict):
            status = str(work.get("status", "")).strip().lower()
            preferred = _CONTINUATION_ACTIONS.get(status, ())
            if preferred:
                if action_name == preferred[0]:
                    return 1.0
                if action_name in preferred:
                    return 0.72
                if action_name == "noop":
                    return 0.18
                return 0.08
        selected = agency.get("selected_need")
        if isinstance(selected, dict):
            need_name = str(selected.get("name", ""))
            preferred = _NEED_ACTIONS.get(need_name, ())
            if action_name in preferred:
                return 0.90
            return 0.38
        focus = agency.get("focus_goal")
        return 0.60 if isinstance(focus, dict) else 0.50

    @staticmethod
    def _reversibility_score(action_name: str, capability: dict[str, Any] | None) -> float:
        if action_name in _ACTION_REVERSIBILITY:
            return _ACTION_REVERSIBILITY[action_name]
        if capability is None:
            return 0.0
        authority = str(capability.get("authority", "")).lower()
        side_effects = str(capability.get("side_effects", "")).lower()
        if "read" in authority and not any(
            word in side_effects for word in ("write", "submit", "mutat")
        ):
            return 0.90
        if "local" in authority:
            return 0.72
        if any(word in authority for word in ("interaction", "external", "account", "write")):
            return 0.40
        return 0.55

    @staticmethod
    def _resource_score(capability: dict[str, Any] | None, success_probability: float) -> float:
        if capability is None:
            return 0.0
        cost = str(capability.get("cost_class", "medium")).strip().lower()
        base = _COST_SCORE.get(cost, 0.50)
        return _unit(base * (0.55 + 0.45 * success_probability))

    def evaluate(
        self,
        *,
        action_name: str,
        prediction: dict[str, Any] | None,
        agency: dict[str, Any] | None,
        capability_catalog: dict[str, Any] | None,
        assurance_accepted: bool,
    ) -> AttractorEvaluation:
        action_name = str(action_name).strip()[:128]
        prediction = prediction if isinstance(prediction, dict) else {}
        agency = agency if isinstance(agency, dict) else {}
        capability = self._capability(capability_catalog, action_name)
        declared_enabled = bool(capability and capability.get("enabled", True))
        feasible = bool(assurance_accepted and action_name and declared_enabled)

        success_probability = _unit(
            prediction.get("action_success_probability", 0.5), 0.5
        )
        information = _saturating_nonnegative(
            prediction.get("expected_information_gain", 0.0)
        )
        reversibility = self._reversibility_score(action_name, capability)
        continuity = self._continuity_score(action_name, agency, reversibility)
        commitment = self._commitment_score(action_name, agency)
        resource_efficiency = self._resource_score(capability, success_probability)
        learning = _unit(
            0.70 * information
            + 0.30 * success_probability * (0.0 if action_name == "noop" else 1.0)
        )

        notes: list[str] = []
        if not assurance_accepted:
            notes.append(
                "CriticAssurance rejected the proposed decision; soft utility is not applicable."
            )
        if capability is None:
            notes.append("Action is absent from the declared capability catalog.")
        elif not bool(capability.get("enabled", True)):
            notes.append("Declared capability is disabled.")
        continuation = agency.get("continuation_work_item")
        if isinstance(continuation, dict):
            notes.append(
                "Unfinished work cursor: "
                f"#{continuation.get('id')} status={continuation.get('status')}."
            )
        selected = agency.get("selected_need")
        if isinstance(selected, dict):
            notes.append(
                "Selected verified pressure: "
                f"{selected.get('name')} severity={_unit(selected.get('severity', 0.0)):.3f}."
            )

        components = {
            "continuity": continuity,
            "commitment": commitment,
            "information_gain": information,
            "reversibility": reversibility,
            "resource_efficiency": resource_efficiency,
            "learning_value": learning,
        }
        score = (
            sum(_WEIGHTS[name] * value for name, value in components.items())
            if feasible
            else None
        )
        return AttractorEvaluation(
            version=ATTRACTOR_VERSION,
            formula=ATTRACTOR_FORMULA,
            score=(round(float(score), 6) if score is not None else None),
            hard_constraints_satisfied=feasible,
            continuity=round(continuity, 6),
            commitment=round(commitment, 6),
            information_gain=round(information, 6),
            reversibility=round(reversibility, 6),
            resource_efficiency=round(resource_efficiency, 6),
            learning_value=round(learning, 6),
            action_name=action_name,
            attractor_fingerprint=self.fingerprint,
            notes=tuple(notes),
        )
