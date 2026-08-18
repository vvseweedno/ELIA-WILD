from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import sqlite3

import yaml


@dataclass(slots=True)
class BrainConfig:
    backend: str
    model_id: str
    base_url: str
    timeout_seconds: float
    max_tokens: int
    temperature: float
    top_p: float
    thinking: bool
    model_revision: str | None = None


@dataclass(slots=True)
class RuntimeConfig:
    state_dir: Path
    cycle_sleep_seconds: float
    max_action_output_chars: int
    weekly_gpu_budget_hours: float
    memory_recall_limit: int
    max_in_session_sleep_seconds: float = 5.0
    auto_checkpoint_path: Path | None = None


@dataclass(slots=True)
class ExecutiveConfig:
    enabled: bool = True
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


@dataclass(slots=True)
class Config:
    identity_name: str
    identity_statement: str
    mission: list[str]
    brain: BrainConfig
    runtime: RuntimeConfig
    raw_tools: dict
    subject_core_path: Path = Path("config/subject_core.yaml")
    continuity_constitution_path: Path = Path("config/continuity_constitution.yaml")
    system_prompt_path: Path = Path("config/system_prompt.md")
    skills_dir: Path = Path("skills")
    branch_id: str = "main"
    executive: ExecutiveConfig = field(default_factory=ExecutiveConfig)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_config_path(config_path: Path, value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (config_path.parent / candidate).resolve()


def _resolve_project_path(config_path: Path, value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    project_root = config_path.parent.parent
    return (project_root / candidate).resolve()


def _persisted_branch_id(state_dir: Path) -> str | None:
    database = state_dir / "memory.sqlite3"
    if not database.is_file():
        return None
    try:
        with sqlite3.connect(database) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='branch_id'").fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    value = str(row[0]).strip()
    return value or None


def load_config(path: str | Path = "config/genesis.yaml") -> Config:
    path = Path(path).expanduser().resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    identity = data["identity"]
    runtime = data["runtime"]
    brain = data["brain"]
    executive = dict(data.get("executive") or {})
    gpu_budget = runtime.get("weekly_gpu_budget_hours", runtime.get("weekly_brain_budget_hours", 30))

    state_dir_raw = os.getenv("ELIA_STATE_DIR", str(runtime["state_dir"]))
    state_dir = _resolve_project_path(path, state_dir_raw)
    auto_checkpoint_raw = os.getenv(
        "ELIA_AUTO_CHECKPOINT_PATH", str(runtime.get("auto_checkpoint_path", "")).strip()
    ).strip()
    auto_checkpoint_path = (
        _resolve_project_path(path, auto_checkpoint_raw) if auto_checkpoint_raw else None
    )

    explicit_branch = os.getenv("ELIA_BRANCH_ID")
    branch_id = (
        explicit_branch.strip()
        if explicit_branch is not None and explicit_branch.strip()
        else _persisted_branch_id(state_dir)
        or str(identity.get("branch_id", "main")).strip()
        or "main"
    )

    subject_core_raw = os.getenv(
        "ELIA_SUBJECT_CORE",
        str(identity.get("subject_core", "subject_core.yaml")),
    )
    constitution_raw = os.getenv(
        "ELIA_CONTINUITY_CONSTITUTION",
        str(identity.get("continuity_constitution", "continuity_constitution.yaml")),
    )
    system_prompt_raw = os.getenv(
        "ELIA_SYSTEM_PROMPT",
        str(identity.get("system_prompt", "system_prompt.md")),
    )
    skills_raw = os.getenv("ELIA_SKILLS_DIR", str(data.get("skills_dir", "skills")))
    model_revision_raw = os.getenv(
        "ELIA_MODEL_REVISION", str(brain.get("model_revision", "")).strip()
    ).strip()

    executive_defaults = ExecutiveConfig()
    executive_config = ExecutiveConfig(
        enabled=_env_bool("ELIA_EXECUTIVE_ENABLED", bool(executive.get("enabled", executive_defaults.enabled))),
        critical_need_threshold=float(executive.get("critical_need_threshold", executive_defaults.critical_need_threshold)),
        maintenance_need_threshold=float(executive.get("maintenance_need_threshold", executive_defaults.maintenance_need_threshold)),
        low_budget_ratio=float(executive.get("low_budget_ratio", executive_defaults.low_budget_ratio)),
        deep_budget_ratio=float(executive.get("deep_budget_ratio", executive_defaults.deep_budget_ratio)),
        deep_focus_threshold=float(executive.get("deep_focus_threshold", executive_defaults.deep_focus_threshold)),
        low_tokens=int(executive.get("low_tokens", executive_defaults.low_tokens)),
        normal_tokens=int(executive.get("normal_tokens", executive_defaults.normal_tokens)),
        deep_tokens=int(executive.get("deep_tokens", executive_defaults.deep_tokens)),
        low_target_brain_seconds=float(executive.get("low_target_brain_seconds", executive_defaults.low_target_brain_seconds)),
        normal_target_brain_seconds=float(executive.get("normal_target_brain_seconds", executive_defaults.normal_target_brain_seconds)),
        deep_target_brain_seconds=float(executive.get("deep_target_brain_seconds", executive_defaults.deep_target_brain_seconds)),
        halt_sleep_seconds=float(executive.get("halt_sleep_seconds", executive_defaults.halt_sleep_seconds)),
        exhausted_sleep_seconds=float(executive.get("exhausted_sleep_seconds", executive_defaults.exhausted_sleep_seconds)),
        conserve_sleep_seconds=float(executive.get("conserve_sleep_seconds", executive_defaults.conserve_sleep_seconds)),
        idle_sleep_seconds=float(executive.get("idle_sleep_seconds", executive_defaults.idle_sleep_seconds)),
        adaptive_thinking=_env_bool(
            "ELIA_EXECUTIVE_ADAPTIVE_THINKING",
            bool(executive.get("adaptive_thinking", executive_defaults.adaptive_thinking)),
        ),
    )

    return Config(
        identity_name=os.getenv("ELIA_IDENTITY_NAME", identity["name"]),
        identity_statement=identity["statement"].strip(),
        mission=list(data.get("mission", [])),
        brain=BrainConfig(
            backend=os.getenv("ELIA_BRAIN", brain["backend"]),
            model_id=os.getenv("ELIA_MODEL_ID", brain["model_id"]),
            base_url=os.getenv("ELIA_MODEL_BASE_URL", brain["base_url"]).rstrip("/"),
            timeout_seconds=float(os.getenv("ELIA_MODEL_TIMEOUT", brain["timeout_seconds"])),
            max_tokens=int(os.getenv("ELIA_MAX_TOKENS", brain["max_tokens"])),
            temperature=float(os.getenv("ELIA_TEMPERATURE", brain["temperature"])),
            top_p=float(os.getenv("ELIA_TOP_P", brain["top_p"])),
            thinking=_env_bool("ELIA_THINKING", bool(brain.get("thinking", False))),
            model_revision=model_revision_raw or None,
        ),
        runtime=RuntimeConfig(
            state_dir=state_dir,
            cycle_sleep_seconds=float(
                os.getenv("ELIA_CYCLE_SLEEP_SECONDS", runtime["cycle_sleep_seconds"])
            ),
            max_action_output_chars=int(runtime["max_action_output_chars"]),
            weekly_gpu_budget_hours=float(os.getenv("ELIA_WEEKLY_GPU_HOURS", gpu_budget)),
            memory_recall_limit=int(runtime["memory_recall_limit"]),
            max_in_session_sleep_seconds=float(
                os.getenv(
                    "ELIA_MAX_IN_SESSION_SLEEP_SECONDS",
                    runtime.get("max_in_session_sleep_seconds", 5),
                )
            ),
            auto_checkpoint_path=auto_checkpoint_path,
        ),
        raw_tools=dict(data.get("tools", {})),
        subject_core_path=_resolve_config_path(path, subject_core_raw),
        continuity_constitution_path=_resolve_config_path(path, constitution_raw),
        system_prompt_path=_resolve_config_path(path, system_prompt_raw),
        skills_dir=_resolve_project_path(path, skills_raw),
        branch_id=branch_id,
        executive=executive_config,
    )
