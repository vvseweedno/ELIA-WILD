from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import shutil
from typing import Any

from .agency import NEED_REGISTRY
from .observations import ObservationStore
from .state_bus import OrganismStateBus
from .world_model import WorldModelStore


@dataclass(frozen=True, slots=True)
class HomeostaticSignal:
    name: str
    severity: float
    reason: str
    response_hint: str
    evidence: dict[str, Any]

    def __post_init__(self) -> None:
        if self.name not in NEED_REGISTRY:
            raise ValueError(f"homeostasis emitted an unregistered need: {self.name!r}")
        if not math.isfinite(float(self.severity)) or not 0.0 <= float(self.severity) <= 1.0:
            raise ValueError("homeostatic severity must be finite and within [0, 1]")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HomeostasisSnapshot:
    checked_at: str
    mode: str
    storage: dict[str, Any]
    state_bus: dict[str, Any]
    sensorium: dict[str, Any]
    epistemics: dict[str, Any]
    digital_body: dict[str, Any]
    metabolism: dict[str, Any]
    signals: tuple[HomeostaticSignal, ...]

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["signals"] = [signal.as_dict() for signal in self.signals]
        return item


class HomeostasisEngine:
    """Model-independent maintenance physiology for ELIA WILD.

    Signals are derived from observable local state. They can raise maintenance
    pressure but cannot create new executable authority or invent external resources.
    Resource pressure is accepted only from a precomputed metabolism snapshot whose
    runway itself is based on verified balances + verified obligations.
    """

    def __init__(
        self,
        state_dir: Path,
        observations: ObservationStore,
        world_model: WorldModelStore,
        state_bus: OrganismStateBus,
        body_diagnostics: dict[str, Any],
        metabolism_snapshot: dict[str, Any] | None = None,
    ):
        self.state_dir = Path(state_dir)
        self.observations = observations
        self.world_model = world_model
        self.state_bus = state_bus
        self.body_diagnostics = dict(body_diagnostics)
        self.metabolism_snapshot = dict(metabolism_snapshot or {})

    @staticmethod
    def _mode(signals: list[HomeostaticSignal]) -> str:
        peak = max((signal.severity for signal in signals), default=0.0)
        if peak >= 0.9:
            return "critical"
        if peak >= 0.6:
            return "strained"
        return "stable"

    @staticmethod
    def _resource_pressure(metabolism: dict[str, Any]) -> list[HomeostaticSignal]:
        signals: list[HomeostaticSignal] = []
        bottleneck = metabolism.get("bottleneck")
        if isinstance(bottleneck, dict) and bottleneck.get("runway_days") is not None:
            try:
                runway = float(bottleneck.get("runway_days", 0.0))
            except (TypeError, ValueError):
                runway = 0.0
            if not math.isfinite(runway):
                runway = 0.0
            runway = max(0.0, runway)
            if runway <= 0:
                severity = 1.0
            elif runway < 3:
                severity = 0.96
            elif runway < 7:
                severity = 0.86
            elif runway < 30:
                severity = 0.64
            else:
                severity = 0.0
            if severity:
                signals.append(
                    HomeostaticSignal(
                        "resource_runway",
                        severity,
                        (
                            f"Verified essential runway for {bottleneck.get('asset')}/"
                            f"{bottleneck.get('unit')} is {runway:.2f} day(s)."
                        ),
                        (
                            "Prefer legitimate value/resource acquisition, cost reduction or an "
                            "authorized substitute that directly improves this constrained resource. "
                            "Do not treat estimates as receipts and do not mix unrelated units."
                        ),
                        {"bottleneck": bottleneck},
                    )
                )

        incomplete_projection = [
            item
            for item in list(metabolism.get("resources") or [])
            if isinstance(item, dict) and item.get("projection_complete") is False
        ]
        if incomplete_projection:
            signals.append(
                HomeostaticSignal(
                    "resource_runway",
                    0.9,
                    "At least one cumulative obligation projection hit its audited event bound; future coverage is unknown.",
                    "Do not treat the truncated horizon as covered. Reduce projection cardinality or obtain an authorized exact accounting projection.",
                    {"incomplete_resources": incomplete_projection[:8]},
                )
            )

        checked_at_raw = str(metabolism.get("checked_at", ""))
        try:
            checked_at = datetime.fromisoformat(checked_at_raw.replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            checked_at = checked_at.astimezone(timezone.utc)
        except ValueError:
            checked_at = datetime.now(timezone.utc)

        # Use the metabolism engine's cumulative recurring cash-flow projection. The
        # earlier implementation copied one aggregate `next_due_covered` flag onto
        # every obligation, which could call a later essential bill covered even after
        # earlier bills had already consumed the same balance.
        not_covered: list[dict[str, Any]] = []
        for resource in list(metabolism.get("resources") or []):
            if not isinstance(resource, dict):
                continue
            due_at = resource.get("first_uncovered_essential_due_at")
            if not due_at:
                continue
            try:
                due = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                due_in = (due.astimezone(timezone.utc) - checked_at).total_seconds()
            except ValueError:
                continue
            if math.isfinite(due_in) and due_in <= 7 * 86_400:
                not_covered.append({**resource, "due_in_seconds": due_in})

        # Compatibility fallback for older stored snapshots that do not yet contain
        # projection fields. It uses per-obligation cumulative coverage annotations,
        # never the unrelated earliest-due flag of the whole resource vector.
        if not not_covered:
            for item in list(metabolism.get("upcoming_verified_obligations") or []):
                if (
                    not isinstance(item, dict)
                    or not bool(item.get("essential", False))
                    or item.get("cumulative_covered") is not False
                ):
                    continue
                try:
                    due_in = float(item.get("due_in_seconds", float("inf")))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(due_in) and due_in <= 7 * 86_400:
                    not_covered.append(item)

        if not_covered:
            nearest = min(
                not_covered,
                key=lambda item: float(item.get("due_in_seconds", float("inf"))),
            )
            due_in = float(nearest.get("due_in_seconds", 0.0))
            severity = 0.99 if due_in <= 86_400 else 0.92
            signals.append(
                HomeostaticSignal(
                    "uncovered_essential_obligation",
                    severity,
                    (
                        f"A verified essential {nearest.get('asset')}/{nearest.get('unit')} "
                        f"cumulative obligation is not covered by the current verified balance and is due "
                        f"in {max(0.0, due_in) / 3600.0:.1f} hour(s)."
                    ),
                    (
                        "Prioritize an authorized action that can cover, reduce, replace or "
                        "truthfully retire this obligation. Scarcity does not broaden authority."
                    ),
                    {"cashflow_projection": nearest},
                )
            )
        return signals

    def evaluate(
        self,
        *,
        ignore_transaction_ids: set[str] | None = None,
    ) -> HomeostasisSnapshot:
        signals: list[HomeostaticSignal] = []
        ignored = set(ignore_transaction_ids or set())

        usage = shutil.disk_usage(self.state_dir)
        free_ratio = usage.free / usage.total if usage.total else 0.0
        storage = {
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
            "free_ratio": free_ratio,
        }
        if free_ratio <= 0.05:
            signals.append(
                HomeostaticSignal(
                    "storage_survival",
                    0.98,
                    f"Only {free_ratio:.1%} of the state filesystem remains free.",
                    "Preserve continuity; avoid large artifacts and diagnose storage pressure before optional work.",
                    storage,
                )
            )
        elif free_ratio <= 0.15:
            signals.append(
                HomeostaticSignal(
                    "storage_conservation",
                    0.72,
                    f"State filesystem free space is below 15% ({free_ratio:.1%}).",
                    "Prefer compact evidence and cleanup proposals over storage-heavy optional work.",
                    storage,
                )
            )

        incomplete = [
            item
            for item in self.state_bus.incomplete(128)
            if str(item["transaction_id"]) not in ignored
        ]
        state_bus = {
            "incomplete_count": len(incomplete),
            "ignored_active_count": len(ignored),
            "oldest_incomplete": incomplete[0] if incomplete else None,
        }
        if incomplete:
            signals.append(
                HomeostaticSignal(
                    "state_reconciliation",
                    min(1.0, 0.88 + 0.02 * len(incomplete)),
                    f"{len(incomplete)} organism transaction(s) are incomplete.",
                    "Reconcile interrupted transitions before optional external activity.",
                    {"transaction_ids": [item["transaction_id"] for item in incomplete[:16]]},
                )
            )

        recent = self.observations.recent(24)
        failed = [item for item in recent if not item.success]
        failure_rate = len(failed) / len(recent) if recent else 0.0
        sensorium = {
            "sample_size": len(recent),
            "failures": len(failed),
            "failure_rate": failure_rate,
            "latest_observation_at": recent[0].observed_at if recent else None,
        }
        if len(recent) >= 6 and failure_rate >= 0.5:
            signals.append(
                HomeostaticSignal(
                    "sensorium_degradation",
                    min(0.9, 0.55 + failure_rate * 0.4),
                    f"Recent capability observations fail at {failure_rate:.0%} over {len(recent)} samples.",
                    "Prefer diagnosis or an alternate healthy sensor/capability instead of blind retries.",
                    sensorium,
                )
            )

        world = self.world_model.snapshot(64)
        contradictions = list(world.get("contradictions") or [])
        epistemics = {
            "active_belief_count": len(world.get("beliefs") or []),
            "contradiction_count": len(contradictions),
        }
        if contradictions:
            signals.append(
                HomeostaticSignal(
                    "epistemic_conflict",
                    min(0.78, 0.45 + 0.05 * len(contradictions)),
                    f"World model contains {len(contradictions)} active contradiction set(s).",
                    "Seek discriminating evidence; do not silently collapse contradictory beliefs into certainty.",
                    {"contradictions": contradictions[:8]},
                )
            )

        digital_body = {
            "enabled": list(self.body_diagnostics.get("enabled") or []),
            "unavailable": dict(self.body_diagnostics.get("unavailable") or {}),
            "capability_count": int(self.body_diagnostics.get("capability_count", 0) or 0),
        }

        signals.extend(self._resource_pressure(self.metabolism_snapshot))
        signals.sort(key=lambda item: (-item.severity, item.name))
        return HomeostasisSnapshot(
            checked_at=datetime.now(timezone.utc).isoformat(),
            mode=self._mode(signals),
            storage=storage,
            state_bus=state_bus,
            sensorium=sensorium,
            epistemics=epistemics,
            digital_body=digital_body,
            metabolism=self.metabolism_snapshot,
            signals=tuple(signals[:16]),
        )
