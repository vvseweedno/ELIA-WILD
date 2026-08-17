from __future__ import annotations

from dataclasses import dataclass
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


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_config_path(config_path: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (config_path.parent / candidate).resolve()


def _resolve_project_path(config_path: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
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
    path = Path(path).resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    identity = data["identity"]
    runtime = data["runtime"]
    brain = data["brain"]
    gpu_budget = runtime.get("weekly_gpu_budget_hours", runtime.get("weekly_brain_budget_hours", 30))
    auto_checkpoint_raw = os.getenv(
        "ELIA_AUTO_CHECKPOINT_PATH", str(runtime.get("auto_checkpoint_path", "")).strip()
    ).strip()

    state_dir = Path(os.getenv("ELIA_STATE_DIR", runtime["state_dir"]))
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
            auto_checkpoint_path=(Path(auto_checkpoint_raw) if auto_checkpoint_raw else None),
        ),
        raw_tools=dict(data.get("tools", {})),
        subject_core_path=_resolve_config_path(path, subject_core_raw),
        continuity_constitution_path=_resolve_config_path(path, constitution_raw),
        system_prompt_path=_resolve_config_path(path, system_prompt_raw),
        skills_dir=_resolve_project_path(path, skills_raw),
        branch_id=branch_id,
    )
