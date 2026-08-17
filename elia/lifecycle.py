from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .chronicle import Chronicle
from .memory import MemoryStore


LifecycleMode = Literal["wake", "hibernate", "halt"]


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    mode: LifecycleMode
    reason: str
    checked_at: str
    next_wake_at: str | None
    seconds_until_wake: float | None
    runtime_hours_remaining: float
    force_wake_requested: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_wake(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_preflight(
    state_dir: Path,
    weekly_gpu_budget_hours: float,
    *,
    force_wake: bool = False,
    now: datetime | None = None,
) -> LifecycleDecision:
    """Decide whether expensive cognition should start, without loading a model.

    This layer intentionally uses only persisted state, Chronicle verification and
    deterministic arithmetic. `force_wake` bypasses schedule timing only; it never
    bypasses integrity or budget guards.
    """

    state_dir = Path(state_dir)
    memory = MemoryStore(state_dir / "memory.sqlite3")
    chronicle = Chronicle(state_dir / "chronicle.jsonl")
    checked = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    valid, error = chronicle.verify()
    limit = max(0.0, float(weekly_gpu_budget_hours))
    used = memory.runtime_seconds_this_week() / 3600.0
    remaining = max(0.0, limit - used)
    next_wake_raw = memory.get_meta("next_wake_at")

    if not valid:
        return LifecycleDecision(
            mode="halt",
            reason=f"Chronicle integrity failure: {error}",
            checked_at=checked.isoformat(),
            next_wake_at=next_wake_raw,
            seconds_until_wake=None,
            runtime_hours_remaining=remaining,
            force_wake_requested=force_wake,
        )

    if limit <= 0 or remaining <= 0:
        return LifecycleDecision(
            mode="hibernate",
            reason="Local weekly GPU runtime budget is exhausted; expensive cognition stays offline.",
            checked_at=checked.isoformat(),
            next_wake_at=next_wake_raw,
            seconds_until_wake=None,
            runtime_hours_remaining=remaining,
            force_wake_requested=force_wake,
        )

    try:
        next_wake = _parse_wake(next_wake_raw)
    except ValueError:
        return LifecycleDecision(
            mode="wake",
            reason="Persisted wake timestamp is invalid; wake once to diagnose and repair scheduler state.",
            checked_at=checked.isoformat(),
            next_wake_at=next_wake_raw,
            seconds_until_wake=None,
            runtime_hours_remaining=remaining,
            force_wake_requested=force_wake,
        )

    if next_wake is not None:
        seconds_until = (next_wake - checked).total_seconds()
        if seconds_until > 0 and not force_wake:
            return LifecycleDecision(
                mode="hibernate",
                reason="The persisted next-wake time is still in the future; do not load the model.",
                checked_at=checked.isoformat(),
                next_wake_at=next_wake.isoformat(),
                seconds_until_wake=seconds_until,
                runtime_hours_remaining=remaining,
                force_wake_requested=False,
            )
        if seconds_until > 0 and force_wake:
            return LifecycleDecision(
                mode="wake",
                reason="Schedule guard bypassed by explicit force-wake request; integrity and budget remain valid.",
                checked_at=checked.isoformat(),
                next_wake_at=next_wake.isoformat(),
                seconds_until_wake=seconds_until,
                runtime_hours_remaining=remaining,
                force_wake_requested=True,
            )

    return LifecycleDecision(
        mode="wake",
        reason="Continuity is valid, compute remains, and cognition is due.",
        checked_at=checked.isoformat(),
        next_wake_at=next_wake.isoformat() if next_wake is not None else None,
        seconds_until_wake=(next_wake - checked).total_seconds() if next_wake is not None else None,
        runtime_hours_remaining=remaining,
        force_wake_requested=force_wake,
    )
