from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from elia.supervisor import ResidentSupervisor


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def make_config_copy(tmp_path: Path) -> Path:
    root = repo_root()
    original = (root / "config" / "genesis.yaml").read_text(encoding="utf-8")
    original = original.replace(
        "subject_core: subject_core.yaml",
        f"subject_core: {root / 'config' / 'subject_core.yaml'}",
    ).replace(
        "continuity_constitution: continuity_constitution.yaml",
        f"continuity_constitution: {root / 'config' / 'continuity_constitution.yaml'}",
    ).replace(
        "system_prompt: system_prompt.md",
        f"system_prompt: {root / 'config' / 'system_prompt.md'}",
    ).replace(
        "skills_dir: skills",
        f"skills_dir: {root / 'skills'}",
    ).replace(
        "state_dir: .elia",
        f"state_dir: {tmp_path / '.elia'}",
    )
    path = tmp_path / "genesis.yaml"
    path.write_text(original, encoding="utf-8")
    return path


def test_unhealthy_vitals_halt_before_child_launch(tmp_path: Path) -> None:
    supervisor = ResidentSupervisor(make_config_copy(tmp_path))
    fake = {
        "healthy": False,
        "organism": {
            "findings": [
                {"organ_id": "persistent_memory", "severity": "critical", "message": "missing"}
            ]
        },
        "continuity_comparison": None,
    }
    supervisor.vitals = SimpleNamespace(
        check=lambda persist=True: SimpleNamespace(healthy=False, as_dict=lambda: fake)
    )
    decision = supervisor.decide()
    assert decision.action == "halt"
    assert decision.child_command is None
    assert "vital-sign" in decision.reason
