from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import time
from typing import Any

from .brain import Brain, Decision, build_brain
from .chronicle import Chronicle
from .config import Config
from .memory import MemoryStore
from .tools import ToolRegistry, ToolResult


class BudgetExhausted(RuntimeError):
    pass


class EliaRuntime:
    """Persistent observe -> decide -> act -> remember runtime."""

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

    def _context(self) -> dict[str, Any]:
        valid, error = self.chronicle.verify()
        recent = [asdict(record) for record in self.memory.recent(self.config.runtime.memory_recall_limit)]
        return {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "identity": {
                "name": self.config.identity_name,
                "statement": self.config.identity_statement,
            },
            "mission": self.config.mission,
            "resources": self.budget(),
            "chronicle_integrity": {"valid": valid, "error": error},
            "recent_memory": recent,
            "last_action": self._load_json_meta("last_action"),
            "available_tools": self.tools.descriptions(),
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

    def cycle(self) -> dict[str, Any]:
        context = self._context()
        decision = self._think(context)
        memory_ids = self._store_model_memories(decision)

        result: ToolResult = self.tools.execute(decision.action_name, decision.action_args)
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

        action_record = {
            "objective": decision.objective,
            "summary": decision.summary,
            "action": {"name": decision.action_name, "args": decision.action_args},
            "result": result_dict,
            "memory_ids": memory_ids,
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
            "resources": self.budget(),
            "sleep_seconds": decision.sleep_seconds,
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
            sleep_for = (
                report["sleep_seconds"]
                if report["sleep_seconds"] is not None
                else self.config.runtime.cycle_sleep_seconds
            )
            if cycles is None or completed < cycles:
                time.sleep(max(0.0, float(sleep_for)))
