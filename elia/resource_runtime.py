from __future__ import annotations

from dataclasses import replace
from typing import Any

from .brain import Decision
from .executive_runtime import ExecutiveOrganismRuntime
from .resource_ecology import ResourceEcologyEngine, ResourceEcologyStore
from .resource_status import resource_ecology_needs
from .tools import ToolResult


class ResourceOrganismRuntime(ExecutiveOrganismRuntime):
    """Genesis 1.4 runtime: Executive organism + typed resource ecology.

    Opportunity estimates are converted into deterministic resource-pressure context
    only after an exact `(asset, unit)` profile exists. Model-originated updates may
    create/adjust those estimate profiles and local work plans, but they cannot mark
    work submitted/accepted/realized or mint verified resources.
    """

    RESOURCE_ECOLOGY_CONTEXT_LIMIT = 12

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        config = args[0] if args else kwargs.get("config")
        if config is None:
            raise TypeError("ResourceOrganismRuntime requires Config as the first argument")
        database = config.runtime.state_dir / "memory.sqlite3"
        self.resource_ecology_store = ResourceEcologyStore(database)
        self.resource_ecology = ResourceEcologyEngine(database)
        super().__init__(*args, **kwargs)

    def _resource_ecology_snapshot(self) -> dict[str, Any]:
        return self.resource_ecology.snapshot(
            self._metabolism_snapshot(),
            limit=self.RESOURCE_ECOLOGY_CONTEXT_LIMIT,
        )

    def _state_components(self) -> dict[str, Any]:
        components = super()._state_components()
        ecology = self.resource_ecology.snapshot(
            components.get("metabolism") or self._metabolism_snapshot(),
            limit=self.RESOURCE_ECOLOGY_CONTEXT_LIMIT,
        )
        components["resource_ecology"] = ecology
        needs = list(components.get("needs") or [])
        names = {str(item.get("name", "")) for item in needs if isinstance(item, dict)}
        for item in resource_ecology_needs(ecology):
            name = str(item.get("name", ""))
            if name and name not in names:
                needs.append(item)
                names.add(name)
        needs.sort(
            key=lambda item: (-float(item.get("severity", 0.0)), str(item.get("name", "")))
        )
        components["needs"] = needs[:20]
        self_model = components.get("self_model")
        if isinstance(self_model, dict):
            self_model["needs"] = [str(item.get("name", "")) for item in components["needs"]]
        return components

    def _context(self) -> dict[str, Any]:
        context = super()._context()
        context["resource_ecology"] = self._resource_ecology_snapshot()
        context["_system_prompt"] = self.prompt_template.render(context)
        return context

    def _apply_opportunity_updates(self, decision: Decision) -> list[dict[str, Any]]:
        base_items: list[dict[str, Any]] = []
        ecology_items: list[dict[str, Any]] = []
        for item in decision.opportunity_updates[:8]:
            if str(item.get("op", "")).strip().lower() in {
                "profile_resource",
                "plan_work",
                "abandon_work",
            }:
                ecology_items.append(item)
            else:
                base_items.append(item)
        changes = super()._apply_opportunity_updates(
            replace(decision, opportunity_updates=base_items[:4])
        )
        for item in ecology_items[:4]:
            op = str(item.get("op", "")).strip().lower()
            try:
                if op == "profile_resource":
                    profile = self.resource_ecology_store.upsert_profile(
                        opportunity_id=int(item.get("opportunity_id")),
                        target_asset=str(item.get("target_asset", "")),
                        target_unit=str(item.get("target_unit", "")),
                        target_amount=float(item.get("target_amount", 0)),
                        eligibility_confidence=min(
                            0.85, max(0.0, float(item.get("eligibility_confidence", 0.5)))
                        ),
                        evidence_quality=min(
                            0.85, max(0.0, float(item.get("evidence_quality", 0.5)))
                        ),
                        evidence=str(item.get("evidence", "")),
                        blockers=(
                            [str(value) for value in item.get("blockers", [])]
                            if isinstance(item.get("blockers"), list)
                            else []
                        ),
                        source="brain",
                    )
                    changes.append(
                        {"ok": True, "op": op, "resource_profile": profile.as_dict()}
                    )
                    continue
                if op == "plan_work":
                    work = self.resource_ecology_store.create_work_item(
                        opportunity_id=int(item.get("opportunity_id")),
                        objective=str(item.get("objective", "")),
                        deliverable_spec=str(item.get("deliverable_spec", "")),
                        acceptance_criteria=str(item.get("acceptance_criteria", "")),
                        estimated_gpu_hours=float(item.get("estimated_gpu_hours", 0.0)),
                        source="brain",
                    )
                    changes.append({"ok": True, "op": op, "work_item": work.as_dict()})
                    continue
                if op == "abandon_work":
                    work = self.resource_ecology_store.abandon_work(
                        int(item.get("work_item_id")),
                        evidence=str(item.get("evidence", "")),
                    )
                    changes.append({"ok": True, "op": op, "work_item": work.as_dict()})
                    continue
                raise ValueError(f"unknown resource ecology operation: {op or '<empty>'}")
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
        if name == "stage_deliverable" and args.get("opportunity_id") is not None:
            opportunity_id = int(args["opportunity_id"])
            work = self.resource_ecology_store.work_for_opportunity(opportunity_id, 16)
            if not any(item.status == "planned" for item in work):
                return ToolResult(
                    False,
                    name,
                    error=(
                        "Resource ecology requires a planned work item before staging a deliverable "
                        f"for opportunity {opportunity_id}."
                    ),
                )
        return super()._execute_action(name, args)

    def cycle(self) -> dict[str, Any]:
        report = super().cycle()
        transition: dict[str, Any] | None = None
        decision = report.get("decision") or {}
        result = report.get("result") or {}
        if decision.get("action_name") == "stage_deliverable" and bool(result.get("ok")):
            data = result.get("data") or {}
            opportunity_id = data.get("opportunity_id")
            artifact_path = data.get("path")
            if opportunity_id is not None and artifact_path:
                try:
                    work = self.resource_ecology_store.attach_staged_deliverable(
                        opportunity_id=int(opportunity_id),
                        artifact_path=str(artifact_path),
                        evidence=(
                            "Local stage_deliverable capability succeeded; this records staging only, "
                            "not external submission."
                        ),
                    )
                    transition = {"ok": True, "event": "deliverable_staged", "work_item": work.as_dict()}
                    self.chronicle.append(
                        "RESOURCE_ECOLOGY",
                        {
                            "event": "deliverable_staged",
                            "opportunity_id": int(opportunity_id),
                            "work_item_id": work.id,
                            "artifact_path": str(artifact_path),
                        },
                    )
                except Exception as exc:
                    transition = {
                        "ok": False,
                        "event": "deliverable_staged",
                        "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
                    }
        report["resource_ecology"] = self._resource_ecology_snapshot()
        if transition is not None:
            report["resource_ecology_transition"] = transition
        return report


EliaResourceRuntime = ResourceOrganismRuntime
