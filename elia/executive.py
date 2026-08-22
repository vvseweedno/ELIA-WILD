from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Literal

from .agency import NEED_REGISTRY
from .canonical import canonical_json
from .sqlite_utils import inserted_row_id


ExecutiveMode = Literal["halt", "hibernate", "maintenance", "resource", "mission", "observe"]
CognitiveTier = Literal["none", "low", "normal", "deep"]


@dataclass(frozen=True, slots=True)
class ExecutivePolicy:
    critical_need_threshold: float = 0.85
    maintenance_need_threshold: float = 0.60
    low_budget_ratio: float = 0.10
    deep_budget_ratio: float = 0.35
    deep_focus_threshold: float = 0.85
    low_tokens: int = 256
    normal_tokens: int = 640
    deep_tokens: int = 1024
    low_target_brain_seconds: float = 6.0
    normal_target_brain_seconds: float = 20.0
    deep_target_brain_seconds: float = 60.0
    halt_sleep_seconds: float = 3600.0
    exhausted_sleep_seconds: float = 3600.0
    conserve_sleep_seconds: float = 900.0
    idle_sleep_seconds: float = 300.0
    adaptive_thinking: bool = True

    def __post_init__(self) -> None:
        for name in (
            "critical_need_threshold",
            "maintenance_need_threshold",
            "low_budget_ratio",
            "deep_budget_ratio",
            "deep_focus_threshold",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and within [0, 1]")
        if self.maintenance_need_threshold > self.critical_need_threshold:
            raise ValueError("maintenance threshold cannot exceed critical threshold")
        if self.low_budget_ratio > self.deep_budget_ratio:
            raise ValueError("low budget ratio cannot exceed deep budget ratio")
        for name in ("low_tokens", "normal_tokens", "deep_tokens"):
            if int(getattr(self, name)) < 32:
                raise ValueError(f"{name} must be at least 32")
        if not self.low_tokens <= self.normal_tokens <= self.deep_tokens:
            raise ValueError("token tiers must be monotonic")
        for name in (
            "low_target_brain_seconds",
            "normal_target_brain_seconds",
            "deep_target_brain_seconds",
            "halt_sleep_seconds",
            "exhausted_sleep_seconds",
            "conserve_sleep_seconds",
            "idle_sleep_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ExecutiveFocus:
    kind: str
    id: int | None
    name: str
    score: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CognitiveBudget:
    tier: CognitiveTier
    wake_brain: bool
    max_tokens: int
    target_brain_seconds: float
    allow_thinking: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExecutivePlan:
    mode: ExecutiveMode
    focus: ExecutiveFocus
    cognitive_budget: CognitiveBudget
    sleep_seconds: float
    budget_ratio: float
    interrupt: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "focus": self.focus.as_dict(),
            "cognitive_budget": self.cognitive_budget.as_dict(),
            "sleep_seconds": self.sleep_seconds,
            "budget_ratio": self.budget_ratio,
            "interrupt": self.interrupt,
            "reasons": list(self.reasons),
        }


class ExecutiveController:
    """Deterministic pre-LLM arbitration for cognition and survival attention.

    It never grants a capability and never selects a concrete side-effecting tool.
    It decides whether the expensive brain should wake, which verified pressure/goal
    deserves attention, and how much inference budget may be spent on the cycle.
    """

    RESOURCE_NEEDS = {
        name
        for name, spec in NEED_REGISTRY.items()
        if spec.category in {"resource", "compute"}
    }
    HARD_STOP_NEEDS = {
        name for name, spec in NEED_REGISTRY.items() if spec.hard_stop
    }
    REPAIR_NEEDS = {
        name
        for name, spec in NEED_REGISTRY.items()
        if spec.category in {"maintenance", "epistemic"}
    }

    def __init__(self, policy: ExecutivePolicy | None = None):
        self.policy = policy or ExecutivePolicy()

    @staticmethod
    def _finite(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @classmethod
    def _need_candidates(cls, context: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for item in context.get("needs") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name not in NEED_REGISTRY:
                continue
            severity = max(0.0, min(1.0, cls._finite(item.get("severity"))))
            candidates.append(
                {
                    "name": name,
                    "severity": severity,
                    "reason": str(item.get("reason", ""))[:2000],
                    "response_hint": str(item.get("response_hint", ""))[:2000],
                }
            )
        candidates.sort(key=lambda item: (-item["severity"], item["name"]))
        return candidates

    @classmethod
    def _goal_candidates(cls, context: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for item in context.get("active_goals") or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "active")).strip().lower()
            if status not in {"active", "blocked"}:
                continue
            priority = max(0.0, min(1.0, cls._finite(item.get("priority"), 0.5)))
            score = priority + (0.12 if status == "active" else -0.18)
            candidates.append(
                {
                    "id": int(item.get("id", 0) or 0),
                    "title": str(item.get("title", ""))[:240],
                    "status": status,
                    "priority": priority,
                    "score": score,
                }
            )
        candidates.sort(key=lambda item: (-item["score"], item["id"]))
        return candidates

    @classmethod
    def _budget_ratio(cls, context: dict[str, Any]) -> tuple[float, float, float]:
        resources = context.get("resources") or {}
        limit = max(0.0, cls._finite(resources.get("weekly_limit_hours")))
        remaining = max(0.0, cls._finite(resources.get("runtime_hours_remaining")))
        ratio = remaining / limit if limit > 0 else 0.0
        return max(0.0, min(1.0, ratio)), remaining, limit

    def _cognitive_budget(
        self,
        *,
        wake: bool,
        budget_ratio: float,
        focus_score: float,
        urgent: bool,
    ) -> CognitiveBudget:
        if not wake:
            return CognitiveBudget("none", False, 0, 0.0, False)
        if budget_ratio <= self.policy.low_budget_ratio:
            return CognitiveBudget(
                "low",
                True,
                self.policy.low_tokens,
                self.policy.low_target_brain_seconds,
                False,
            )
        if (
            budget_ratio >= self.policy.deep_budget_ratio
            and focus_score >= self.policy.deep_focus_threshold
            and urgent
        ):
            return CognitiveBudget(
                "deep",
                True,
                self.policy.deep_tokens,
                self.policy.deep_target_brain_seconds,
                self.policy.adaptive_thinking,
            )
        return CognitiveBudget(
            "normal",
            True,
            self.policy.normal_tokens,
            self.policy.normal_target_brain_seconds,
            False,
        )

    def plan(self, context: dict[str, Any]) -> ExecutivePlan:
        needs = self._need_candidates(context)
        goals = self._goal_candidates(context)
        ratio, remaining, limit = self._budget_ratio(context)
        drift = context.get("identity_drift") or {}
        chronicle = context.get("chronicle_integrity") or {}
        top_need = needs[0] if needs else None

        hard_reason: str | None = None
        hard_name = ""
        if chronicle.get("valid") is False:
            hard_name = "continuity_integrity"
            hard_reason = f"Chronicle integrity is invalid: {chronicle.get('error')}"
        elif str(drift.get("status", "")).lower() == "critical":
            hard_name = "identity_drift"
            hard_reason = "Identity drift monitor reports a critical continuity failure."
        elif top_need and top_need["name"] in self.HARD_STOP_NEEDS:
            hard_name = top_need["name"]
            hard_reason = top_need["reason"]

        if hard_reason:
            focus = ExecutiveFocus("need", None, hard_name, 1.0, hard_reason)
            return ExecutivePlan(
                "halt",
                focus,
                self._cognitive_budget(wake=False, budget_ratio=ratio, focus_score=1.0, urgent=True),
                self.policy.halt_sleep_seconds,
                ratio,
                True,
                (hard_reason, "Normal cognition is suppressed until continuity is restored."),
            )

        if limit <= 0 or remaining <= 0:
            reason = "Weekly GPU runtime budget is exhausted; preserve state until compute resets."
            focus = ExecutiveFocus("need", None, "compute_survival", 1.0, reason)
            return ExecutivePlan(
                "hibernate",
                focus,
                self._cognitive_budget(wake=False, budget_ratio=ratio, focus_score=1.0, urgent=True),
                self.policy.exhausted_sleep_seconds,
                ratio,
                True,
                (reason,),
            )

        if top_need and top_need["severity"] >= self.policy.maintenance_need_threshold:
            name = top_need["name"]
            if name in self.RESOURCE_NEEDS:
                mode: ExecutiveMode = "resource"
            else:
                mode = "maintenance"
            focus = ExecutiveFocus(
                "need",
                None,
                name,
                top_need["severity"],
                top_need["reason"],
            )
            urgent = top_need["severity"] >= self.policy.critical_need_threshold
            cognitive = self._cognitive_budget(
                wake=True,
                budget_ratio=ratio,
                focus_score=top_need["severity"],
                urgent=urgent,
            )
            sleep = (
                self.policy.conserve_sleep_seconds
                if ratio <= self.policy.low_budget_ratio and not urgent
                else 0.0
            )
            return ExecutivePlan(
                mode,
                focus,
                cognitive,
                sleep,
                ratio,
                urgent,
                (
                    f"Highest verified pressure is {name!r} at severity {top_need['severity']:.3f}.",
                    top_need["response_hint"],
                ),
            )

        if goals:
            goal = goals[0]
            focus = ExecutiveFocus(
                "goal",
                goal["id"],
                goal["title"],
                min(1.0, max(0.0, goal["score"])),
                f"Highest deterministic durable-goal score; status={goal['status']}, priority={goal['priority']:.3f}.",
            )
            cognitive = self._cognitive_budget(
                wake=True,
                budget_ratio=ratio,
                focus_score=focus.score,
                urgent=goal["priority"] >= self.policy.deep_focus_threshold,
            )
            return ExecutivePlan(
                "mission",
                focus,
                cognitive,
                0.0,
                ratio,
                False,
                (focus.reason,),
            )

        if top_need:
            focus = ExecutiveFocus(
                "need",
                None,
                top_need["name"],
                top_need["severity"],
                top_need["reason"],
            )
            cognitive = self._cognitive_budget(
                wake=True,
                budget_ratio=ratio,
                focus_score=top_need["severity"],
                urgent=False,
            )
            return ExecutivePlan(
                "observe",
                focus,
                cognitive,
                self.policy.idle_sleep_seconds if ratio <= self.policy.low_budget_ratio else 0.0,
                ratio,
                False,
                ("No higher-priority maintenance pressure or durable goal is active.",),
            )

        reason = "No verified pressure or durable goal currently justifies expensive cognition."
        focus = ExecutiveFocus("idle", None, "idle", 0.0, reason)
        return ExecutivePlan(
            "hibernate",
            focus,
            self._cognitive_budget(wake=False, budget_ratio=ratio, focus_score=0.0, urgent=False),
            self.policy.idle_sleep_seconds,
            ratio,
            False,
            (reason,),
        )


class ExecutiveStore:
    """Audit trail for pre-inference executive decisions and measured outcomes."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS executive_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT NULL,
                    context_digest TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    focus_kind TEXT NOT NULL,
                    focus_id INTEGER NULL,
                    focus_name TEXT NOT NULL,
                    focus_score REAL NOT NULL,
                    cognitive_tier TEXT NOT NULL,
                    brain_wake INTEGER NOT NULL,
                    max_tokens INTEGER NOT NULL,
                    target_brain_seconds REAL NOT NULL,
                    allow_thinking INTEGER NOT NULL,
                    sleep_seconds REAL NOT NULL,
                    budget_ratio REAL NOT NULL,
                    interrupt INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL,
                    brain_seconds_used REAL NULL,
                    action_name TEXT NULL,
                    result_ok INTEGER NULL,
                    outcome_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_executive_cycles_created
                    ON executive_cycles(id DESC);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def context_digest(context: dict[str, Any]) -> str:
        public = {key: value for key, value in context.items() if not str(key).startswith("_")}
        raw = canonical_json(public)
        return sha256(raw.encode("utf-8")).hexdigest()

    def record(self, plan: ExecutivePlan, context: dict[str, Any]) -> int:
        budget = plan.cognitive_budget
        focus = plan.focus
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO executive_cycles(
                    created_at, context_digest, mode, focus_kind, focus_id, focus_name,
                    focus_score, cognitive_tier, brain_wake, max_tokens,
                    target_brain_seconds, allow_thinking, sleep_seconds, budget_ratio,
                    interrupt, reasons_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(),
                    self.context_digest(context),
                    plan.mode,
                    focus.kind,
                    focus.id,
                    focus.name,
                    focus.score,
                    budget.tier,
                    1 if budget.wake_brain else 0,
                    budget.max_tokens,
                    budget.target_brain_seconds,
                    1 if budget.allow_thinking else 0,
                    plan.sleep_seconds,
                    plan.budget_ratio,
                    1 if plan.interrupt else 0,
                    json.dumps(plan.reasons, ensure_ascii=False),
                ),
            )
            return inserted_row_id(cur, operation="executive plan insert")

    def resolve(
        self,
        row_id: int,
        *,
        brain_seconds_used: float,
        action_name: str,
        result_ok: bool,
        outcome: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE executive_cycles
                SET resolved_at=?, brain_seconds_used=?, action_name=?, result_ok=?, outcome_json=?
                WHERE id=?
                """,
                (
                    self._now(),
                    max(0.0, float(brain_seconds_used)),
                    str(action_name)[:128],
                    1 if result_ok else 0,
                    json.dumps(outcome or {}, ensure_ascii=False, sort_keys=True)[:16000],
                    int(row_id),
                ),
            )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM executive_cycles ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]
