from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from elia.brain import MockBrain
from elia.checkpoint import recover_interrupted_restore
from elia.config import BrainConfig, Config, RuntimeConfig
from elia.continuity_runtime import ELIARuntime
from elia.epistemic_runtime import EpistemicOrganismRuntime
from elia.pipeline import CanonicalRuntimePipeline
from elia.transition_kernel import StateWriterLock, StateWriterLockTimeout


def _attempt_restore_with_bounded_writer_lock(
    state_dir: Path,
    output: multiprocessing.Queue,
) -> None:
    try:
        with StateWriterLock(state_dir, timeout_seconds=0.25):
            recover_interrupted_restore(state_dir, lock_held=True)
    except StateWriterLockTimeout:
        output.put("blocked")
    except BaseException as exc:
        output.put(f"error:{type(exc).__name__}:{exc}")
    else:
        output.put("completed")


class CaptureBrain(MockBrain):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[dict] = []

    def decide(self, context: dict):
        self.contexts.append(context)
        return super().decide(context)


def _config(tmp_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "system_prompt.md").write_text(
        (root / "config" / "system_prompt.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (policy / "autonomy_attractor.md").write_text(
        (root / "config" / "autonomy_attractor.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return Config(
        identity_name="ELIA",
        identity_statement="Canonical attractor runtime test seed.",
        mission=["preserve continuity", "increase verified autonomy"],
        brain=BrainConfig(
            backend="mock",
            model_id="mock",
            base_url="http://127.0.0.1:8000/v1",
            timeout_seconds=5,
            max_tokens=128,
            temperature=0.0,
            top_p=1.0,
            thinking=False,
        ),
        runtime=RuntimeConfig(
            state_dir=tmp_path / ".elia",
            cycle_sleep_seconds=0,
            max_action_output_chars=16000,
            weekly_gpu_budget_hours=30,
            memory_recall_limit=12,
        ),
        raw_tools={
            "http_get": {"enabled": False},
            "workspace": {"enabled": True},
            "work_ports": {"enabled": False, "ports": {}},
            "body": {
                "browser": {"enabled": False},
                "process": {"enabled": False},
                "mcp": {"enabled": False, "servers": {}},
                "jsonrpc": {"enabled": False, "endpoints": {}},
            },
        },
        subject_core_path=root / "config" / "subject_core.yaml",
        continuity_constitution_path=root / "config" / "continuity_constitution.yaml",
        system_prompt_path=policy / "system_prompt.md",
        epistemic_path=root / "config" / "epistemic.yaml",
        skills_dir=root / "skills",
    )


def test_attractor_contract_is_in_canonical_system_context_and_report(tmp_path: Path) -> None:
    config = _config(tmp_path)
    brain = CaptureBrain()
    runtime = ELIARuntime(config, brain=brain)

    report = runtime.cycle()

    assert brain.contexts
    system_prompt = brain.contexts[0]["_system_prompt"]
    assert "# ELIA WILD Autonomy Attractor" in system_prompt
    assert "maximum verified agency per unit" in system_prompt
    assert runtime.attractor.fingerprint in system_prompt
    assert report["autonomy_attractor"]["attractor_fingerprint"] == runtime.attractor.fingerprint
    assert report["cognitive_policy_fingerprint"] == runtime.memory.get_meta(
        "cognitive_policy_fingerprint"
    )
    assert len(report["cognitive_policy_fingerprint"]) == 64


def test_cognitive_policy_fingerprint_is_stable_across_process_restart(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = ELIARuntime(config, brain=CaptureBrain())
    first.cycle()
    policy_fingerprint = first.memory.get_meta("cognitive_policy_fingerprint")
    attractor_fingerprint = first.memory.get_meta("autonomy_attractor_fingerprint")

    second = ELIARuntime(config, brain=CaptureBrain())

    assert second.memory.get_meta("cognitive_policy_fingerprint") == policy_fingerprint
    assert second.memory.get_meta("autonomy_attractor_fingerprint") == attractor_fingerprint
    assert policy_fingerprint == second._cognitive_policy_fingerprint()


def test_constructor_holds_writer_across_all_base_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    context = multiprocessing.get_context("fork")
    outcomes: list[str] = []

    def pause_inside_base_initialization(self, *args, **kwargs) -> None:
        output = context.Queue()
        process = context.Process(
            target=_attempt_restore_with_bounded_writer_lock,
            args=(config.runtime.state_dir, output),
        )
        process.start()
        process.join(timeout=3)
        assert process.exitcode == 0
        outcomes.append(output.get(timeout=1))
        raise RuntimeError("intentional constructor barrier")

    monkeypatch.setattr(EpistemicOrganismRuntime, "__init__", pause_inside_base_initialization)
    with pytest.raises(RuntimeError, match="intentional constructor barrier"):
        ELIARuntime(config, brain=CaptureBrain())
    assert outcomes == ["blocked"]

    # The constructor's finally path must release the lease even after a failed boot.
    output = context.Queue()
    process = context.Process(
        target=_attempt_restore_with_bounded_writer_lock,
        args=(config.runtime.state_dir, output),
    )
    process.start()
    process.join(timeout=3)
    assert process.exitcode == 0
    assert output.get(timeout=1) == "completed"


def test_constructor_keeps_writer_through_final_pipeline_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    context = multiprocessing.get_context("fork")
    original_init = CanonicalRuntimePipeline.__init__
    outcomes: list[str] = []

    def probe_at_pipeline_tail(self, stages) -> None:
        output = context.Queue()
        process = context.Process(
            target=_attempt_restore_with_bounded_writer_lock,
            args=(config.runtime.state_dir, output),
        )
        process.start()
        process.join(timeout=3)
        assert process.exitcode == 0
        outcomes.append(output.get(timeout=1))
        original_init(self, stages)

    monkeypatch.setattr(CanonicalRuntimePipeline, "__init__", probe_at_pipeline_tail)
    runtime = ELIARuntime(config, brain=CaptureBrain())
    assert runtime.pipeline.describe()
    assert outcomes == ["blocked"]

    output = context.Queue()
    process = context.Process(
        target=_attempt_restore_with_bounded_writer_lock,
        args=(config.runtime.state_dir, output),
    )
    process.start()
    process.join(timeout=3)
    assert process.exitcode == 0
    assert output.get(timeout=1) == "completed"
