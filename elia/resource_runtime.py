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

    Local deliverable staging is interpreted inside `_execute_action`, before the base
    cognitive cycle commits its accepted transition. There is no post-commit ecology
    mutation that can disagree with an already committed action result.
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
            self_model["needs"] = [
                str(item.get("name", "")) for item in components["needs"]
            ]
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
                    opportunity_id_raw = item.get("opportunity_id")
                    if opportunity_id_raw is None:
                        raise ValueError("resource profile opportunity_id is required")
                    profile = self.resource_ecology_store.upsert_profile(
                        opportunity_id=int(opportunity_id_raw),
                        target_asset=str(item.get("target_asset", "")),
                        target_unit=str(item.get("target_unit", "")),
                        target_amount=float(item.get("target_amount", 0)),
                        eligibility_confidence=min(
                            0.85,
                            max(0.0, float(item.get("eligibility_confidence", 0.5))),
                        ),
                        evidence_quality=min(
                            0.85,
                            max(0.0, float(item.get("evidence_quality", 0.5))),
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
                    opportunity_id_raw = item.get("opportunity_id")
                    if opportunity_id_raw is None:
                        raise ValueError("work plan opportunity_id is required")
                    created_work = self.resource_ecology_store.create_work_item(
                        opportunity_id=int(opportunity_id_raw),
                        objective=str(item.get("objective", "")),
                        deliverable_spec=str(item.get("deliverable_spec", "")),
                        acceptance_criteria=str(item.get("acceptance_criteria", "")),
                        estimated_gpu_hours=float(item.get("estimated_gpu_hours", 0.0)),
                        source="brain",
                    )
                    changes.append(
                        {"ok": True, "op": op, "work_item": created_work.as_dict()}
                    )
                    continue
                if op == "abandon_work":
                    work_item_id_raw = item.get("work_item_id")
                    if work_item_id_raw is None:
                        raise ValueError("abandon_work work_item_id is required")
                    abandoned_work = self.resource_ecology_store.abandon_work(
                        int(work_item_id_raw),
                        evidence=str(item.get("evidence", "")),
                    )
                    changes.append(
                        {"ok": True, "op": op, "work_item": abandoned_work.as_dict()}
                    )
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
        opportunity_id: int | None = None
        if name == "stage_deliverable" and args.get("opportunity_id") is not None:
            opportunity_id = int(args["opportunity_id"])
            planned_work = self.resource_ecology_store.work_for_opportunity(
                opportunity_id, 16
            )
            if not any(item.status == "planned" for item in planned_work):
                return ToolResult(
                    False,
                    name,
                    error=(
                        "Resource ecology requires a planned work item before staging a deliverable "
                        f"for opportunity {opportunity_id}."
                    ),
                )

        result = super()._execute_action(name, args)
        if name != "stage_deliverable" or not result.ok or opportunity_id is None:
            return result
        data = dict(result.data or {}) if isinstance(result.data, dict) else {}
        artifact_path = data.get("path")
        if not artifact_path:
            return ToolResult(
                False,
                name,
                data,
                "stage_deliverable succeeded without returning its local artifact path",
            )
        try:
            staged_work = self.resource_ecology_store.attach_staged_deliverable(
                opportunity_id=opportunity_id,
                artifact_path=str(artifact_path),
                evidence=(
                    "Local stage_deliverable capability succeeded; staging is part of "
                    "the same cognitive transition and is not external submission."
                ),
            )
        except Exception as exc:
            return ToolResult(
                False,
                name,
                data,
                f"ResourceEcologyStageError: {type(exc).__name__}: {str(exc)[:1000]}",
            )
        data["resource_ecology_transition"] = {
            "ok": True,
            "event": "deliverable_staged",
            "work_item": staged_work.as_dict(),
        }
        return ToolResult(True, name, data, result.error)

    def cycle(self) -> dict[str, Any]:
        report = super().cycle()
        report["resource_ecology"] = self._resource_ecology_snapshot()
        result = report.get("result") or {}
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict) and isinstance(
            data.get("resource_ecology_transition"), dict
        ):
            report["resource_ecology_transition"] = data["resource_ecology_transition"]
        return report


EliaResourceRuntime = ResourceOrganismRuntime
