from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

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


@dataclass(slots=True)
class Config:
    identity_name: str
    identity_statement: str
    mission: list[str]
    brain: BrainConfig
    runtime: RuntimeConfig
    raw_tools: dict


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: str | Path = "config/genesis.yaml") -> Config:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    identity = data["identity"]
    runtime = data["runtime"]
    brain = data["brain"]
    gpu_budget = runtime.get("weekly_gpu_budget_hours", runtime.get("weekly_brain_budget_hours", 30))

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
            state_dir=Path(os.getenv("ELIA_STATE_DIR", runtime["state_dir"])),
            cycle_sleep_seconds=float(
                os.getenv("ELIA_CYCLE_SLEEP_SECONDS", runtime["cycle_sleep_seconds"])
            ),
            max_action_output_chars=int(runtime["max_action_output_chars"]),
            weekly_gpu_budget_hours=float(os.getenv("ELIA_WEEKLY_GPU_HOURS", gpu_budget)),
            memory_recall_limit=int(runtime["memory_recall_limit"]),
        ),
        raw_tools=dict(data.get("tools", {})),
    )
