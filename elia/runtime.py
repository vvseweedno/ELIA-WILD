from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Any

from . import __version__
from .assurance import CriticAssurance, IdentityDriftMonitor
from .autonomy import derive_needs
from .brain import Brain, Decision, build_brain
from .chronicle import Chronicle
from .config import Config
from .economy import EconomyStore
from .identity import IdentityBundle, IdentityStore, build_self_model_snapshot
from .memory import MemoryStore
from .metacognition import MetacognitionStore
from .prompting import PromptTemplate
from .recall import RecallEngine
from .self_model import SelfHypothesisStore
from .skills import SkillRegistry
from .tools import ToolRegistry, ToolResult


class BudgetExhausted(RuntimeError):
    pass


class EliaRuntime:
    """Persistent ELIA organism.

    verify -> reconstruct self -> recall -> predict -> decide -> assure -> act ->
    compare outcome -> update adaptive state -> persist lineage/Chronicle.
    """

    MAX_ACTIVE_GOALS = 32
    MAX_ACTIVE_OPPORTUNITIES = 64
    CAPABILITY_FAILURE_THRESHOLD = 3
    DEGRADATION_EXEMPT = {"noop", "self_check", "propose_repair", "stage_deliverable"}

    def __init__(self, config: Config, brain: Brain | None = None):
        self.config = config
        state_dir = config.runtime.state_dir
        state_dir.mkdir(parents=True, exist_ok=True)

        database = state_dir / "memory.sqlite3"
        self.memory = MemoryStore(database)
        self.recall = RecallEngine(self.memory)
        self.economy = EconomyStore(database)
        self.identity_store = IdentityStore(database)
        self.self_hypotheses = SelfHypothesisStore(database)
        self.metacognition = MetacognitionStore(database)
        self.chronicle = Chronicle(state_dir / "chronicle.jsonl")
        self.tools = ToolRegistry(state_dir / "workspace", config.raw_tools)
        self.skills = SkillRegistry(config.skills_dir)
        self.identity = IdentityBundle.load(
            config.subject_core_path,
            config.continuity_constitution_path,
        )
        self.prompt_template = PromptTemplate.load(config.system_prompt_path)
        self.assurance = CriticAssurance()
        self.drift_monitor = IdentityDriftMonitor(self.identity)
        self._brain: Brain | None = brain

        if config.identity_name != self.identity.name:
            raise RuntimeError(
                f"configured identity name {config.identity_name!r} does not match Subject Core {self.identity.name!r}"
            )

        self._last_runtime_accounting = time.monotonic()
        self._boot()

    @property
    def brain_loaded(self) -> bool:
        return self._brain is not None

    def _get_brain(self) -> Brain:
        if self._brain is None:
            self._brain = build_brain(self.config.brain)
            loaded_at = datetime.now(timezone.utc).isoformat()
            self.memory.set_meta("last_brain_loaded_at", loaded_at)
            self.chronicle.append(
                "BRAIN_LOAD",
                {
                    "backend": self.config.brain.backend,
                    "model_id": self.config.brain.model_id,
                    "identity_fingerprint": self.identity.fingerprint,
                    "prompt_fingerprint": self.prompt_template.fingerprint,
                },
            )
        return self._brain

    def _lineage_consistent(self) -> bool:
        valid, _ = self.identity_store.verify_lineage(
            expected_identity_fingerprint=self.identity.fingerprint,
            expected_branch_id=self.config.branch_id,
        )
        return valid

    def _boot(self) -> None:
        valid, error = self.chronicle.verify()
        if not valid:
            raise RuntimeError(f"Chronicle integrity failure: {error}")

        identity_valid, identity_error = self.identity_store.verify_identity_fingerprint(
            self.identity.fingerprint
        )
        if not identity_valid:
            raise RuntimeError(f"Identity continuity failure: {identity_error}")

        lineage_valid, lineage_error = self.identity_store.verify_lineage(
            expected_identity_fingerprint=self.identity.fingerprint,
            expected_branch_id=self.config.branch_id,
        )
        if not lineage_valid:
            raise RuntimeError(f"Lineage continuity failure: {lineage_error}")

        boot_count = int(self.memory.get_meta("boot_count", "0") or "0") + 1
        self.memory.set_meta("boot_count", str(boot_count))
        self.memory.set_meta("lifecycle_state", "awake")
        self.memory.set_meta("identity_bundle_fingerprint", self.identity.fingerprint)
        self.memory.set_meta("subject_core_fingerprint", self.identity.subject_core_fingerprint)
        self.memory.set_meta("constitution_fingerprint", self.identity.constitution_fingerprint)
        self.memory.set_meta("prompt_fingerprint", self.prompt_template.fingerprint)
        self.memory.set_meta("body_version", __version__)
        self.memory.set_meta("branch_id", self.config.branch_id)

        first_boot = self.memory.get_meta("genesis_initialized") != "1"
        if first_boot:
            self.memory.remember(
                "self",
                self.config.identity_statement,
                importance=1.0,
                source="genesis",
                metadata={
                    "immutable_seed": True,
                    "identity_fingerprint": self.identity.fingerprint,
                },
            )
            self.memory.set_meta("genesis_initialized", "1")

        checkpoint_digest = self.memory.get_meta("checkpoint_digest")
        restored_from = self.memory.get_meta("restored_from_checkpoint")
        self.identity_store.record_lineage(
            event="boot",
            branch_id=self.config.branch_id,
            body_version=__version__,
            brain_backend=self.config.brain.backend,
            model_id=self.config.brain.model_id,
            identity_fingerprint=self.identity.fingerprint,
            checkpoint_digest=checkpoint_digest,
            parent_checkpoint_digest=restored_from,
            note="runtime boot after Chronicle, identity and lineage verification",
        )

        boot_entry = self.chronicle.append(
            "BOOT",
            {
                "boot_count": boot_count,
                "first_boot": first_boot,
                "identity": self.config.identity_name,
                "identity_id": self.identity.identity_id,
                "identity_fingerprint": self.identity.fingerprint,
                "subject_core_fingerprint": self.identity.subject_core_fingerprint,
                "constitution_fingerprint": self.identity.constitution_fingerprint,
                "prompt_fingerprint": self.prompt_template.fingerprint,
                "branch_id": self.config.branch_id,
                "body_version": __version__,
                "brain_backend": self.config.brain.backend,
                "model_id": self.config.brain.model_id,
                "brain_loaded": self.brain_loaded,
                "active_goal_count": len(self.memory.active_goals(self.MAX_ACTIVE_GOALS + 1)),
                "active_opportunity_count": len(
                    self.economy.active_opportunities(self.MAX_ACTIVE_OPPORTUNITIES + 1)
                ),
                "adaptive_self_hypothesis_count": len(self.self_hypotheses.active(256)),
                "previous_intended_wake_at": self.memory.get_meta("next_wake_at"),
                "declared_capabilities": sorted(self.tools.catalog()),
                "declared_skills": self.skills.names(),
            },
        )
        self.memory.set_meta("last_boot_chronicle_seq", str(boot_entry.seq))
        self._record_self_model(source="boot")

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

    def skill_state(self, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
        capabilities = capabilities or self.capability_state()
        return self.skills.prompt_catalog(
            capabilities["catalog"],
            capabilities["health"],
        )

    def _state_components(self) -> dict[str, Any]:
        valid, error = self.chronicle.verify()
        goals = self.memory.active_goals(16)
        resources = self.budget()
        capabilities = self.capability_state()
        economy = self.economy.snapshot(16)
        hypotheses = self.self_hypotheses.snapshot(24)
        needs = [
            need.as_dict()
            for need in derive_needs(
                self.memory,
                chronicle_valid=valid,
                budget=resources,
                active_goals=goals,
                capability_health=capabilities["health"],
                capability_catalog=capabilities["catalog"],
                economy=economy,
            )
        ]
        skills = self.skill_state(capabilities)
        previous = self.identity_store.latest_self_model()
        uncertainties: list[str] = []
        if not self.memory.get_meta("checkpoint_digest"):
            uncertainties.append("No authenticated durable checkpoint has yet been anchored for this branch.")
        if not self._lineage_consistent():
            uncertainties.append("Current lineage relation is not consistent with the loaded identity bundle.")
        for hypothesis in hypotheses:
            if hypothesis.get("domain") == "uncertainty" or hypothesis.get("status") == "uncertain":
                uncertainties.append(str(hypothesis.get("proposition", "")))

        snapshot = build_self_model_snapshot(
            bundle=self.identity,
            body_version=__version__,
            brain_backend=self.config.brain.backend,
            model_id=self.config.brain.model_id,
            lifecycle_state=self.memory.get_meta("lifecycle_state", "unknown") or "unknown",
            active_goal_count=len(goals),
            active_opportunity_count=len(economy["active_opportunities"]),
            capability_health=capabilities["health"],
            needs=needs,
            verified_resources=economy["verified_resources"],
            uncertainties=uncertainties,
            adaptive_hypotheses=hypotheses,
        )
        current = snapshot.as_dict()
        drift = self.drift_monitor.compare(
            previous,
            current,
            lineage_consistent=self._lineage_consistent(),
        )
        if drift.status == "critical":
            needs = [
                {
                    "name": "identity_drift",
                    "severity": 1.0,
                    "reason": "; ".join(drift.hard_failures),
                    "response_hint": "Do not broaden action; preserve state and resolve identity/lineage inconsistency.",
                }
            ] + needs
            current["needs"] = [item["name"] for item in needs]
        return {
            "chronicle": {"valid": valid, "error": error},
            "goals": goals,
            "resources": resources,
            "capabilities": capabilities,
            "economy": economy,
            "needs": needs,
            "skills": skills,
            "self_hypotheses": hypotheses,
            "metacognition": self.metacognition.calibration(100),
            "self_model": current,
            "drift": drift.as_dict(),
        }

    def _record_self_model(self, *, source: str) -> tuple[int, str, dict[str, Any]]:
        components = self._state_components()
        snapshot = dict(components["self_model"])
        row_id, fingerprint = self.identity_store.record_self_model(snapshot, source=source)
        self.memory.set_meta("self_model_fingerprint", fingerprint)
        self.memory.set_meta("last_drift_report", json.dumps(components["drift"], sort_keys=True))
        return row_id, fingerprint, components

    def _memory_queries(self, components: dict[str, Any]) -> list[str]:
        queries: list[str] = []
        for goal in components["goals"]:
            queries.extend([goal.title, goal.description])
        for need in components["needs"]:
            queries.extend([str(need.get("name", "")), str(need.get("reason", ""))])
        for opportunity in components["economy"].get("active_opportunities", [])[:6]:
            queries.extend(
                [str(opportunity.get("title", "")), str(opportunity.get("notes", ""))]
            )
        for hypothesis in components["self_hypotheses"][:8]:
            queries.append(str(hypothesis.get("proposition", "")))
        return [item for item in queries if item]

    def _context(self) -> dict[str, Any]:
        components = self._state_components()
        recalled = self.recall.recall(
            queries=self._memory_queries(components),
            limit=self.config.runtime.memory_recall_limit,
        )
        context: dict[str, Any] = {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "identity": {
                "name": self.config.identity_name,
                "id": self.identity.identity_id,
                "statement": self.config.identity_statement,
                "branch_id": self.config.branch_id,
                "body_version": __version__,
            },
            "identity_contract": self.identity.prompt_contract(),
            "self_model": components["self_model"],
            "self_hypotheses": components["self_hypotheses"],
            "identity_drift": components["drift"],
            "mission": self.config.mission,
            "resources": components["resources"],
            "economy": components["economy"],
            "metacognition": components["metacognition"],
            "needs": components["needs"],
            "scheduler": self._scheduler_state(),
            "chronicle_integrity": components["chronicle"],
            "active_goals": [asdict(goal) for goal in components["goals"]],
            "recent_memory": recalled,
            "chronological_recent_memory": [
                asdict(record) for record in self.memory.recent(min(6, self.config.runtime.memory_recall_limit))
            ],
            "last_action": self._load_json_meta("last_action"),
            "capabilities": components["capabilities"],
            "skills": components["skills"],
            "lineage_head": (
                asdict(self.identity_store.last_lineage())
                if self.identity_store.last_lineage() is not None
                else None
            ),
        }
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

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
        brain = self._get_brain()
        started = time.monotonic()
        try:
            return brain.decide(context)
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
                    metadata={
                        "identity_fingerprint": self.identity.fingerprint,
                        "model_id": self.config.brain.model_id,
                    },
                )
            )
        return ids

    def _apply_self_updates(self, decision: Decision) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for item in decision.self_updates[:4]:
            op = str(item.get("op", "")).strip().lower()
            try:
                if op == "create":
                    # Model-originated autobiographical hypotheses are deliberately
                    # confidence-capped until later evidence updates them.
                    confidence = min(0.75, max(0.0, float(item.get("confidence", 0.5))))
                    hypothesis_id = self.self_hypotheses.create(
                        domain=str(item.get("domain", "other")),
                        proposition=str(item.get("proposition", "")),
                        confidence=confidence,
                        evidence=str(item.get("evidence", "")),
                        source="brain",
                    )
                    changes.append(
                        {"ok": True, "op": "create", "hypothesis_id": hypothesis_id}
                    )
                    continue
                if op == "update":
                    hypothesis_id = int(item.get("id"))
                    status = str(item.get("status", "active")).strip().lower()
                    # Only a trusted runtime/adapter should eventually elevate a
                    # model-originated claim to externally verified truth. The self
                    # hypothesis layer therefore treats `supported` as active evidence,
                    # not as an immutable fact.
                    if status == "supported":
                        status = "active"
                    confidence = (
                        min(0.90, max(0.0, float(item["confidence"])))
                        if item.get("confidence") is not None
                        else None
                    )
                    updated = self.self_hypotheses.update(
                        hypothesis_id,
                        confidence=confidence,
                        status=status,
                        evidence=str(item.get("evidence", "")),
                        event="brain_update",
                    )
                    changes.append(
                        {
                            "ok": True,
                            "op": "update",
                            "hypothesis_id": updated.id,
                            "status": updated.status,
                            "confidence": updated.confidence,
                        }
                    )
                    continue
                raise ValueError(f"unknown self update operation: {op or '<empty>'}")
            except Exception as exc:
                changes.append(
                    {
                        "ok": False,
                        "op": op or "unknown",
                        "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
                    }
                )
        return changes

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

    def _apply_opportunity_updates(self, decision: Decision) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for item in decision.opportunity_updates[:4]:
            op = str(item.get("op", "")).strip().lower()
            try:
                if op == "create":
                    active = self.economy.active_opportunities(self.MAX_ACTIVE_OPPORTUNITIES + 1)
                    title = str(item.get("title", "")).strip()
                    source_url = str(item.get("source_url", "")).strip()
                    duplicate = next(
                        (
                            opportunity
                            for opportunity in active
                            if opportunity.title.casefold() == title.casefold()
                            and opportunity.source_url == source_url
                        ),
                        None,
                    )
                    if duplicate is not None:
                        changes.append(
                            {
                                "ok": True,
                                "op": "create",
                                "opportunity_id": duplicate.id,
                                "deduplicated": True,
                            }
                        )
                        continue
                    if len(active) >= self.MAX_ACTIVE_OPPORTUNITIES:
                        raise ValueError(
                            f"active opportunity limit reached: {self.MAX_ACTIVE_OPPORTUNITIES}"
                        )
                    opportunity_id = self.economy.create_opportunity(
                        title=title,
                        kind=str(item.get("kind", "other")),
                        source_url=source_url,
                        evidence=str(item.get("evidence", "")),
                        estimated_value=float(item.get("estimated_value", 0)),
                        estimated_cost_value=float(item.get("estimated_cost_value", 0)),
                        unit=str(item.get("unit", "VALUE_UNIT")),
                        probability=float(item.get("probability", 0)),
                        estimated_gpu_hours=float(item.get("estimated_gpu_hours", 0)),
                        expires_at=item.get("expires_at"),
                        notes=str(item.get("notes", "")),
                        source="brain",
                    )
                    changes.append(
                        {"ok": True, "op": "create", "opportunity_id": opportunity_id}
                    )
                    continue

                if op == "update":
                    opportunity_id = int(item.get("id"))
                    updated = self.economy.update_opportunity(
                        opportunity_id,
                        status=(str(item["status"]) if item.get("status") is not None else None),
                        estimated_value=(
                            float(item["estimated_value"])
                            if item.get("estimated_value") is not None
                            else None
                        ),
                        estimated_cost_value=(
                            float(item["estimated_cost_value"])
                            if item.get("estimated_cost_value") is not None
                            else None
                        ),
                        probability=(
                            float(item["probability"])
                            if item.get("probability") is not None
                            else None
                        ),
                        estimated_gpu_hours=(
                            float(item["estimated_gpu_hours"])
                            if item.get("estimated_gpu_hours") is not None
                            else None
                        ),
                        evidence=str(item.get("evidence", "")),
                        notes=(str(item["notes"]) if item.get("notes") is not None else None),
                        event="brain_update",
                    )
                    changes.append(
                        {
                            "ok": True,
                            "op": "update",
                            "opportunity_id": updated.id,
                            "status": updated.status,
                            "expected_net_value": updated.expected_net_value,
                            "value_per_gpu_hour": updated.value_per_gpu_hour,
                        }
                    )
                    continue

                raise ValueError(f"unknown opportunity operation: {op or '<empty>'}")
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
        try:
            result = self.tools.execute(name, args)
            return result
        finally:
            duration_ms = (time.monotonic() - started) * 1000.0
            result_obj = locals().get("result")
            self.memory.record_capability_event(
                name,
                ok=(result_obj.ok if isinstance(result_obj, ToolResult) else False),
                executed=True,
                duration_ms=duration_ms,
                error=(
                    result_obj.error
                    if isinstance(result_obj, ToolResult) and result_obj.error
                    else ""
                ),
            )
