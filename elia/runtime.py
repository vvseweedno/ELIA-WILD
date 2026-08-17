from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Any

from .autonomy import derive_needs
from .brain import Brain, Decision, build_brain
from .chronicle import Chronicle
from .config import Config
from .memory import MemoryStore
from .tools import ToolRegistry, ToolResult


class BudgetExhausted(RuntimeError):
    pass


class EliaRuntime:
    """Persistent observe -> assess needs -> decide -> act -> remember runtime."""

    MAX_ACTIVE_GOALS = 32
    CAPABILITY_FAILURE_THRESHOLD = 3
    DEGRADATION_EXEMPT = {"noop", "self_check", "propose_repair"}

    def __init__(self, config: Config, brain: Brain | None = None):
        self.config = config
        state_dir = config.runtime.state_dir
        state_dir.mkdir(parents=True, exist_ok=True)

        self.memory = MemoryStore(state_dir / "memory.sqlite3")
        self.chronicle = Chronicle(state_dir / "chronicle.jsonl")
        self.tools = ToolRegistry(state_dir / "workspace", config.raw_tools)
        self.brain = brain or build_brain(config.brain)

        self._last_runtime_accounting = time.monotonic()
        self._boot()

    def _boot(self) -> None:
        valid, error = self.chronicle.verify()
        if not valid:
            raise RuntimeError(f"Chronicle integrity failure: {error}")

        boot_count = int(self.memory.get_meta("boot_count", "0") or "0") + 1
        self.memory.set_meta("boot_count", str(boot_count))

        first_boot = self.memory.get_meta("genesis_initialized") != "1"
        if first_boot:
            self.memory.remember(
                "self",
                self.config.identity_statement,
                importance=1.0,
                source="genesis",
                metadata={"immutable_seed": True},
            )
            self.memory.set_meta("genesis_initialized", "1")

        self.chronicle.append(
            "BOOT",
            {
                "boot_count": boot_count,
                "first_boot": first_boot,
                "identity": self.config.identity_name,
                "brain_backend": self.config.brain.backend,
                "model_id": self.config.brain.model_id,
                "active_goal_count": len(self.memory.active_goals(self.MAX_ACTIVE_GOALS + 1)),
                "previous_intended_wake_at": self.memory.get_meta("next_wake_at"),
                "declared_capabilities": sorted(self.tools.catalog()),
            },
        )

    def _account_runtime(self) -> float:
        now = time.monotonic()
        delta = max(0.0, now - self._last_runtime_accounting)
        self._last_runtime_accounting = now
        if delta:
            self.memory.add_runtime_seconds(delta)
        return delta

    def budget(self) -> dict[str, float]:
        self._account_runtime()
        limit_seconds = self.config.runtime.weekly_gpu_budget_hours * 3600.0
        runtime_seconds = self.memory.runtime_seconds_this_week()
        brain_seconds = self.memory.brain_seconds_this_week()
        return {
            "weekly_limit_hours": self.config.runtime.weekly_gpu_budget_hours,
            "runtime_hours_used": runtime_seconds / 3600.0,
            "brain_hours_used": brain_seconds / 3600.0,
            "runtime_hours_remaining": max(0.0, (limit_seconds - runtime_seconds) / 3600.0),
        }

    def _scheduler_state(self) -> dict[str, Any]:
        raw = self.memory.get_meta("next_wake_at")
        if not raw:
            return {"next_wake_at": None, "lateness_seconds": None}
        try:
            intended = datetime.fromisoformat(raw)
            if intended.tzinfo is None:
                intended = intended.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            lateness = max(0.0, (now - intended.astimezone(timezone.utc)).total_seconds())
            return {"next_wake_at": raw, "lateness_seconds": lateness}
        except ValueError:
            return {"next_wake_at": raw, "lateness_seconds": None, "invalid": True}

    def _schedule_next_wake(self, requested: float | None) -> tuple[float, str]:
        delay = self.config.runtime.cycle_sleep_seconds if requested is None else float(requested)
        delay = max(0.0, min(delay, 86400.0))
        wake_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        wake_raw = wake_at.isoformat()
        self.memory.set_meta("next_wake_at", wake_raw)
        self.memory.set_meta("last_sleep_seconds", f"{delay:.6f}")
        return delay, wake_raw

    def capability_state(self) -> dict[str, Any]:
        catalog = self.tools.catalog()
        health = self.memory.capability_health_all(list(catalog), window=20)
        return {"catalog": catalog, "health": health}

    def _context(self) -> dict[str, Any]:
        valid, error = self.chronicle.verify()
        recent = [asdict(record) for record in self.memory.recent(self.config.runtime.memory_recall_limit)]
        goal_records = self.memory.active_goals(16)
        goals = [asdict(goal) for goal in goal_records]
        resources = self.budget()
        capabilities = self.capability_state()
        needs = [
            need.as_dict()
            for need in derive_needs(
                self.memory,
                chronicle_valid=valid,
                budget=resources,
                active_goals=goal_records,
                capability_health=capabilities["health"],
            )
        ]
        return {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "identity": {
                "name": self.config.identity_name,
                "statement": self.config.identity_statement,
            },
            "mission": self.config.mission,
            "resources": resources,
            "needs": needs,
            "scheduler": self._scheduler_state(),
            "chronicle_integrity": {"valid": valid, "error": error},
            "active_goals": goals,
            "recent_memory": recent,
            "last_action": self._load_json_meta("last_action"),
            "capabilities": capabilities,
        }

    def _load_json_meta(self, key: str) -> Any:
        raw = self.memory.get_meta(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def _check_budget(self) -> None:
        resources = self.budget()
        if resources["runtime_hours_remaining"] <= 0:
            self.chronicle.append("BUDGET_EXHAUSTED", resources)
            raise BudgetExhausted("Weekly GPU runtime budget exhausted")

    def _think(self, context: dict[str, Any]) -> Decision:
        self._check_budget()
        started = time.monotonic()
        try:
            return self.brain.decide(context)
        finally:
            elapsed = time.monotonic() - started
            self.memory.add_brain_seconds(elapsed)
            self._account_runtime()

    def _store_model_memories(self, decision: Decision) -> list[int]:
        ids: list[int] = []
        for item in decision.memories:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            kind = str(item.get("kind", "lesson"))[:64]
            try:
                importance = float(item.get("importance", 0.5))
            except (TypeError, ValueError):
                importance = 0.5
            ids.append(
                self.memory.remember(
                    kind,
                    content[:8000],
                    importance=importance,
                    source="brain",
                )
            )
        return ids

    def _apply_goal_updates(self, decision: Decision) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for item in decision.goal_updates[:4]:
            op = str(item.get("op", "")).strip().lower()
            try:
                if op == "create":
                    title = str(item.get("title", "")).strip()
                    active = self.memory.active_goals(self.MAX_ACTIVE_GOALS + 1)
                    duplicate = next(
                        (goal for goal in active if goal.title.casefold() == title.casefold()),
                        None,
                    )
                    if duplicate is not None:
                        changes.append(
                            {
                                "ok": True,
                                "op": "create",
                                "goal_id": duplicate.id,
                                "deduplicated": True,
                            }
                        )
                        continue
                    if len(active) >= self.MAX_ACTIVE_GOALS:
                        raise ValueError(f"active goal limit reached: {self.MAX_ACTIVE_GOALS}")
                    parent_raw = item.get("parent_id")
                    parent_id = int(parent_raw) if parent_raw is not None else None
                    goal_id = self.memory.create_goal(
                        title,
                        str(item.get("description", "")),
                        priority=float(item.get("priority", 0.5)),
                        source="brain",
                        parent_id=parent_id,
                    )
                    changes.append({"ok": True, "op": "create", "goal_id": goal_id})
                    continue

                if op in {"update", "complete", "abandon", "block", "activate"}:
                    goal_id = int(item.get("id"))
                    status = item.get("status")
                    if op == "complete":
                        status = "completed"
                    elif op == "abandon":
                        status = "abandoned"
                    elif op == "block":
                        status = "blocked"
                    elif op == "activate":
                        status = "active"
                    evidence = str(item.get("evidence", "")).strip()
                    if status in {"completed", "abandoned"} and not evidence:
                        raise ValueError("completing or abandoning a goal requires evidence")
                    updated = self.memory.update_goal(
                        goal_id,
                        status=str(status) if status is not None else None,
                        priority=(float(item["priority"]) if item.get("priority") is not None else None),
                        description=(
                            str(item["description"])
                            if item.get("description") is not None
                            else None
                        ),
                        event=op,
                        evidence=evidence,
                    )
                    changes.append(
                        {
                            "ok": True,
                            "op": op,
                            "goal_id": updated.id,
                            "status": updated.status,
                            "priority": updated.priority,
                        }
                    )
                    continue

                raise ValueError(f"unknown goal operation: {op or '<empty>'}")
            except Exception as exc:
                changes.append(
                    {
                        "ok": False,
                        "op": op or "unknown",
                        "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
                    }
                )
        return changes

    def _execute_action(self, name: str, args: dict[str, Any]) -> ToolResult:
        health = self.memory.capability_health(name, window=20)
        if (
            name not in self.DEGRADATION_EXEMPT
            and int(health["consecutive_failures"]) >= self.CAPABILITY_FAILURE_THRESHOLD
        ):
            error = (
                f"Capability temporarily suppressed after {health['consecutive_failures']} "
                "consecutive failures. Use self_check, an alternative capability, or propose_repair "
                "instead of blind retry."
            )
            self.memory.record_capability_event(
                name,
                ok=False,
                executed=False,
                duration_ms=0.0,
                error=error,
            )
            return ToolResult(
                False,
                name,
                data={"suppressed": True, "health": health},
                error=error,
            )

        started = time.monotonic()
        result = self.tools.execute(name, args)
        duration_ms = (time.monotonic() - started) * 1000.0
        self.memory.record_capability_event(
            name,
            ok=result.ok,
            executed=True,
            duration_ms=duration_ms,
            error=result.error or "",
        )
        return result

    def cycle(self) -> dict[str, Any]:
        context = self._context()
        decision = self._think(context)
        memory_ids = self._store_model_memories(decision)

        result = self._execute_action(decision.action_name, decision.action_args)
        result_dict = result.as_dict()
        max_chars = self.config.runtime.max_action_output_chars
        serialized = json.dumps(result_dict, ensure_ascii=False, sort_keys=True)
        if len(serialized) > max_chars:
            result_dict = {
                "ok": result.ok,
                "tool": result.tool,
                "data": {"truncated_result": serialized[:max_chars]},
                "error": result.error,
            }

        goal_changes = self._apply_goal_updates(decision)
        sleep_seconds, next_wake_at = self._schedule_next_wake(decision.sleep_seconds)
        action_record = {
            "objective": decision.objective,
            "summary": decision.summary,
            "action": {"name": decision.action_name, "args": decision.action_args},
            "result": result_dict,
            "memory_ids": memory_ids,
            "goal_changes": goal_changes,
            "capability_health": self.memory.capability_health(decision.action_name),
            "scheduler": {
                "sleep_seconds": sleep_seconds,
                "next_wake_at": next_wake_at,
            },
        }
        self.memory.set_meta("last_action", json.dumps(action_record, ensure_ascii=False, sort_keys=True))
        self.memory.remember(
            "action_result",
            json.dumps(action_record, ensure_ascii=False, sort_keys=True)[:12000],
            importance=0.6 if result.ok else 0.8,
            source="runtime",
        )
        entry = self.chronicle.append("CYCLE", action_record)
        self._account_runtime()

        return {
            "chronicle_seq": entry.seq,
            "decision": {
                "objective": decision.objective,
                "summary": decision.summary,
                "action_name": decision.action_name,
            },
            "result": result_dict,
            "goal_changes": goal_changes,
            "active_goals": [asdict(goal) for goal in self.memory.active_goals(16)],
            "capability_health": self.memory.capability_health(decision.action_name),
            "resources": self.budget(),
            "sleep_seconds": sleep_seconds,
            "next_wake_at": next_wake_at,
        }

    def run(self, cycles: int | None = None) -> None:
        completed = 0
        while cycles is None or completed < cycles:
            try:
                report = self.cycle()
            except BudgetExhausted:
                return
            except KeyboardInterrupt:
                self.chronicle.append("SHUTDOWN", {"reason": "keyboard_interrupt"})
                return
            except Exception as exc:
                error = {"type": type(exc).__name__, "message": str(exc)[:4000]}
                self.memory.remember(
                    "runtime_error",
                    json.dumps(error, ensure_ascii=False),
                    importance=0.9,
                    source="runtime",
                )
                self.chronicle.append("ERROR", error)
                if cycles is not None:
                    raise
                sleep_for = min(max(self.config.runtime.cycle_sleep_seconds, 1.0), 300.0)
                time.sleep(sleep_for)
                continue

            completed += 1
            if cycles is None or completed < cycles:
                time.sleep(max(0.0, float(report["sleep_seconds"])))
