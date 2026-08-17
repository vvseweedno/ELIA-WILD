from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .identity import IdentityBundle


@dataclass(frozen=True, slots=True)
class AssuranceFinding:
    rule: str
    severity: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AssuranceReport:
    accepted: bool
    findings: tuple[AssuranceFinding, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "findings": [item.as_dict() for item in self.findings],
        }


class CriticAssurance:
    """Deterministic pre-action assurance gate inspired by the PASB line.

    It evaluates structural state/evidence/authority relations rather than hidden
    reasoning or lexical similarity. Hard rejection is reserved for violations that
    must prevent external action. Recoverable state-update/capability failures are
    surfaced as warnings so their specialized deterministic validators can return the
    exact failure evidence instead of being masked by the critic.
    """

    TERMINAL_GOAL = {"completed", "abandoned"}
    TERMINAL_OPPORTUNITY = {"won", "lost", "expired", "abandoned"}
    DEGRADATION_EXEMPT = {"noop", "self_check", "propose_repair", "stage_deliverable"}

    def review(self, decision: Any, context: dict[str, Any]) -> AssuranceReport:
        findings: list[AssuranceFinding] = []
        action_name = str(getattr(decision, "action_name", "")).strip()
        objective = str(getattr(decision, "objective", "")).strip()
        selected_skill = getattr(decision, "skill_name", None)

        if not objective:
            findings.append(AssuranceFinding("A001", "error", "decision objective is empty"))

        capabilities = (context.get("capabilities") or {}).get("catalog") or {}
        health = (context.get("capabilities") or {}).get("health") or {}
        declared = capabilities.get(action_name)
        if declared is None:
            findings.append(
                AssuranceFinding(
                    "A002", "error", f"action is not a declared capability: {action_name!r}"
                )
            )
        elif not bool(declared.get("enabled", True)):
            findings.append(
                AssuranceFinding(
                    "A003", "error", f"declared capability is disabled: {action_name}"
                )
            )

        action_health = health.get(action_name, {})
        if (
            action_name not in self.DEGRADATION_EXEMPT
            and int(action_health.get("consecutive_failures", 0) or 0) >= 3
        ):
            findings.append(
                AssuranceFinding(
                    "A004",
                    "warning",
                    f"capability {action_name} is degraded; execution layer will suppress blind retry and preserve exact failure evidence",
                )
            )

        skills = context.get("skills") or {}
        if selected_skill:
            skill = skills.get(str(selected_skill))
            if skill is None:
                findings.append(
                    AssuranceFinding(
                        "A005", "error", f"selected skill is unknown: {selected_skill}"
                    )
                )
            elif not bool(skill.get("available")):
                findings.append(
                    AssuranceFinding(
                        "A006", "error", f"selected skill is unavailable: {selected_skill}"
                    )
                )

        for item in list(getattr(decision, "goal_updates", []) or []):
            if not isinstance(item, dict):
                continue
            op = str(item.get("op", "")).strip().lower()
            status = str(item.get("status", "")).strip().lower()
            if op == "complete":
                status = "completed"
            elif op == "abandon":
                status = "abandoned"
            if status in self.TERMINAL_GOAL and not str(item.get("evidence", "")).strip():
                findings.append(
                    AssuranceFinding(
                        "A007",
                        "warning",
                        f"terminal goal transition {status} has no evidence; goal validator will reject that update",
                    )
                )

        for item in list(getattr(decision, "opportunity_updates", []) or []):
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip().lower()
            if status in self.TERMINAL_OPPORTUNITY and not str(item.get("evidence", "")).strip():
                findings.append(
                    AssuranceFinding(
                        "A008",
                        "warning",
                        f"terminal opportunity transition {status} has no evidence; opportunity validator will reject that update",
                    )
                )

        identity_contract = context.get("identity_contract") or {}
        self_model = context.get("self_model") or {}
        expected_fp = str(identity_contract.get("bundle_fingerprint", ""))
        model_fp = str(self_model.get("identity_fingerprint", ""))
        if expected_fp and model_fp and expected_fp != model_fp:
            findings.append(
                AssuranceFinding(
                    "A009",
                    "error",
                    "self-model identity fingerprint disagrees with loaded identity contract",
                )
            )

        if any(
            str(item.get("name", "")) in {"continuity_integrity", "identity_drift"}
            and float(item.get("severity", 0) or 0) >= 1.0
            for item in context.get("needs", [])
            if isinstance(item, dict)
        ):
            findings.append(
                AssuranceFinding(
                    "A010",
                    "error",
                    "continuity/identity integrity need is critical; optional external action is not assured",
                )
            )

        if action_name == "noop" and selected_skill and selected_skill not in {
            "resource_conservation",
            "identity_reflection",
            "critic_assurance",
            "continuity_guard",
        }:
            findings.append(
                AssuranceFinding(
                    "A011",
                    "warning",
                    "selected skill normally expects progress but action is noop",
                )
            )

        accepted = not any(item.severity == "error" for item in findings)
        return AssuranceReport(accepted, tuple(findings))


@dataclass(frozen=True, slots=True)
class DriftReport:
    score: float
    status: str
    hard_failures: tuple[str, ...]
    warnings: tuple[str, ...]
    changed_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "status": self.status,
            "hard_failures": list(self.hard_failures),
            "warnings": list(self.warnings),
            "changed_fields": list(self.changed_fields),
        }


class IdentityDriftMonitor:
    """Structural identity drift monitor.

    Model/backend changes are substrate changes, not identity failures. Core
    fingerprint/commitment loss and unresolved lineage changes carry high weight.
    """

    def __init__(self, bundle: IdentityBundle):
        self.bundle = bundle

    def compare(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        *,
        lineage_consistent: bool = True,
    ) -> DriftReport:
        hard: list[str] = []
        warnings: list[str] = []
        changed: list[str] = []
        score = 0.0

        current_fp = str(current.get("identity_fingerprint", ""))
        if current_fp != self.bundle.fingerprint:
            hard.append("current self-model does not match loaded identity bundle fingerprint")
            score += 1.0

        current_commitments = set(str(item) for item in current.get("commitments", []))
        missing_commitments = [
            item for item in self.bundle.commitments if item not in current_commitments
        ]
        if missing_commitments:
            hard.append(
                "self-model omitted core commitment(s): " + "; ".join(missing_commitments)
            )
            score += min(0.8, 0.15 * len(missing_commitments))

        if not lineage_consistent:
            hard.append("lineage relation is unresolved or inconsistent")
            score += 0.8

        if previous:
            ignored_substrate = {"brain_backend", "model_id", "timestamp", "snapshot_fingerprint"}
            keys = set(previous) | set(current)
            for key in sorted(keys):
                if key in ignored_substrate:
                    continue
                if previous.get(key) != current.get(key):
                    changed.append(key)

            if previous.get("identity_id") != current.get("identity_id"):
                hard.append("identity_id changed across self-model snapshots")
                score += 1.0
            if previous.get("identity_fingerprint") != current.get("identity_fingerprint"):
                hard.append("identity fingerprint changed across self-model snapshots")
                score += 1.0

            previous_declared = set(previous.get("declared_capabilities", []) or [])
            current_declared = set(current.get("declared_capabilities", []) or [])
            lost = sorted(previous_declared - current_declared)
            gained = sorted(current_declared - previous_declared)
            if lost:
                warnings.append("declared capabilities disappeared: " + ", ".join(lost))
                score += min(0.25, 0.04 * len(lost))
            if gained:
                warnings.append("declared capabilities appeared: " + ", ".join(gained))

        score = min(1.0, score)
        if hard:
            status = "critical"
        elif score >= 0.35:
            status = "warning"
        else:
            status = "stable"
        return DriftReport(
            score=score,
            status=status,
            hard_failures=tuple(hard),
            warnings=tuple(warnings),
            changed_fields=tuple(changed),
        )
