from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .tools import ToolResult


BeforeCycle = Callable[[], None]
ContextStage = Callable[[dict[str, Any]], dict[str, Any]]
ActionTerminal = Callable[[str, dict[str, Any]], ToolResult]
ActionStage = Callable[[str, dict[str, Any], ActionTerminal], ToolResult]


@dataclass(frozen=True, slots=True)
class RuntimeStage:
    """One named composition stage in the canonical production pipeline."""

    name: str
    before_cycle: BeforeCycle | None = None
    enrich_context: ContextStage | None = None
    execute_action: ActionStage | None = None


class CanonicalRuntimePipeline:
    """Ordered composition boundary above historical Genesis compatibility ancestry.

    New production concerns are registered as independent stages instead of creating a
    deeper runtime subclass. Context enrichers run in declared order. Action stages are
    middleware: the first stage receives a continuation that invokes the next stage and
    ultimately the historical body dispatcher. Lifecycle preconditions run before a
    cognitive transition starts.

    This intentionally provides a migration seam rather than pretending the historical
    inheritance tree vanished in one release. Genesis 1.7.1 routes all *new* authority,
    external-effect, memory-policy and cognitive-finalization behavior through this
    composition object; later releases can lift older organs behind the same interface.
    """

    def __init__(self, stages: list[RuntimeStage] | tuple[RuntimeStage, ...]) -> None:
        names = [str(stage.name).strip() for stage in stages]
        if any(not name for name in names):
            raise ValueError("runtime pipeline stage names are required")
        if len(names) != len(set(names)):
            raise ValueError("runtime pipeline stage names must be unique")
        self.stages = tuple(stages)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": stage.name,
                "before_cycle": stage.before_cycle is not None,
                "enrich_context": stage.enrich_context is not None,
                "execute_action": stage.execute_action is not None,
            }
            for stage in self.stages
        ]

    def run_before_cycle(self) -> None:
        for stage in self.stages:
            if stage.before_cycle is not None:
                stage.before_cycle()

    def enrich(self, context: dict[str, Any]) -> dict[str, Any]:
        current = context
        for stage in self.stages:
            if stage.enrich_context is not None:
                next_context = stage.enrich_context(current)
                if next_context is not current:
                    current = next_context
        return current

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        terminal: ActionTerminal,
    ) -> ToolResult:
        action_stages = [stage for stage in self.stages if stage.execute_action is not None]

        def invoke(index: int, action_name: str, action_args: dict[str, Any]) -> ToolResult:
            if index >= len(action_stages):
                return terminal(action_name, action_args)
            stage = action_stages[index]
            assert stage.execute_action is not None
            return stage.execute_action(
                action_name,
                action_args,
                lambda next_name, next_args: invoke(index + 1, next_name, next_args),
            )

        return invoke(0, str(name), dict(args))
