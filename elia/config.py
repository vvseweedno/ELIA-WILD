from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import os
import re
import sqlite3

import yaml

from .paths import resolve_entry_config


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

    def __post_init__(self) -> None:
        if self.backend not in {"mock", "openai_compatible", "transformers_4bit"}:
            raise ValueError(f"unsupported brain backend: {self.backend!r}")
        if not str(self.model_id).strip():
            raise ValueError("brain model_id must be non-empty")
        if not str(self.base_url).strip():
            raise ValueError("brain base_url must be non-empty")
        self.timeout_seconds = _finite_positive(
            "brain timeout_seconds", self.timeout_seconds
        )
        if not isinstance(self.max_tokens, int) or isinstance(self.max_tokens, bool):
            raise ValueError("brain max_tokens must be a positive integer")
        if self.max_tokens < 1:
            raise ValueError("brain max_tokens must be a positive integer")
        self.temperature = _finite_in_range(
            "brain temperature", self.temperature, minimum=0.0
        )
        self.top_p = _finite_in_range(
            "brain top_p",
            self.top_p,
            minimum=0.0,
            maximum=1.0,
            minimum_inclusive=False,
        )
        revision = str(self.model_revision or "").strip()
        if revision and re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError("brain model_revision must be a full lowercase Git commit SHA")
        if self.backend == "transformers_4bit" and not revision:
            raise ValueError("transformers_4bit requires an immutable model_revision")
        self.model_revision = revision or None


@dataclass(slots=True)
class RuntimeConfig:
    state_dir: Path
    cycle_sleep_seconds: float
    max_action_output_chars: int
    weekly_gpu_budget_hours: float
    memory_recall_limit: int
    max_in_session_sleep_seconds: float = 5.0
    auto_checkpoint_path: Path | None = None

    def __post_init__(self) -> None:
        self.state_dir = Path(self.state_dir)
        self.cycle_sleep_seconds = _finite_non_negative(
            "runtime cycle_sleep_seconds", self.cycle_sleep_seconds
        )
        if not isinstance(self.max_action_output_chars, int) or isinstance(
            self.max_action_output_chars, bool
        ):
            raise ValueError("runtime max_action_output_chars must be a positive integer")
        if self.max_action_output_chars < 1:
            raise ValueError("runtime max_action_output_chars must be a positive integer")
        self.weekly_gpu_budget_hours = _finite_non_negative(
            "runtime weekly_gpu_budget_hours", self.weekly_gpu_budget_hours
        )
        if not isinstance(self.memory_recall_limit, int) or isinstance(
            self.memory_recall_limit, bool
        ):
            raise ValueError("runtime memory_recall_limit must be a positive integer")
        if self.memory_recall_limit < 1:
            raise ValueError("runtime memory_recall_limit must be a positive integer")
        self.max_in_session_sleep_seconds = _finite_non_negative(
            "runtime max_in_session_sleep_seconds", self.max_in_session_sleep_seconds
        )
        if self.auto_checkpoint_path is not None:
            self.auto_checkpoint_path = Path(self.auto_checkpoint_path)
            state = self.state_dir.expanduser().resolve()
            checkpoint = self.auto_checkpoint_path.expanduser().resolve()
            if checkpoint == state or checkpoint.is_relative_to(state):
                raise ValueError("auto checkpoint path must be outside runtime state_dir")


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

    def __post_init__(self) -> None:
        for name in (
            "critical_need_threshold",
            "maintenance_need_threshold",
            "low_budget_ratio",
            "deep_budget_ratio",
            "deep_focus_threshold",
        ):
            setattr(
                self,
                name,
                _finite_in_range(f"executive {name}", getattr(self, name), 0.0, 1.0),
            )
        if self.maintenance_need_threshold > self.critical_need_threshold:
            raise ValueError("executive maintenance threshold cannot exceed critical threshold")
        if self.low_budget_ratio > self.deep_budget_ratio:
            raise ValueError("executive low budget ratio cannot exceed deep budget ratio")
        for name in ("low_tokens", "normal_tokens", "deep_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 32:
                raise ValueError(f"executive {name} must be an integer of at least 32")
        if not self.low_tokens <= self.normal_tokens <= self.deep_tokens:
            raise ValueError("executive token tiers must be monotonic")
        for name in (
            "low_target_brain_seconds",
            "normal_target_brain_seconds",
            "deep_target_brain_seconds",
            "halt_sleep_seconds",
            "exhausted_sleep_seconds",
            "conserve_sleep_seconds",
            "idle_sleep_seconds",
        ):
            setattr(
                self,
                name,
                _finite_non_negative(f"executive {name}", getattr(self, name)),
            )


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
    epistemic_path: Path = Path("config/epistemic.yaml")
    skills_dir: Path = Path("skills")
    branch_id: str = "main"
    executive: ExecutiveConfig = field(default_factory=ExecutiveConfig)

    def __post_init__(self) -> None:
        if not str(self.identity_name).strip():
            raise ValueError("identity name must be non-empty")
        if not str(self.identity_statement).strip():
            raise ValueError("identity statement must be non-empty")
        if not isinstance(self.mission, list) or not self.mission:
            raise ValueError("mission must contain at least one statement")
        if any(not isinstance(item, str) or not item.strip() for item in self.mission):
            raise ValueError("mission statements must be non-empty strings")
        if not str(self.branch_id).strip():
            raise ValueError("branch_id must be non-empty")
        if len(str(self.branch_id)) > 256:
            raise ValueError("branch_id is too long")
        if not isinstance(self.raw_tools, dict):
            raise ValueError("tools configuration must be an object")


def _finite_in_range(
    name: str,
    value: float,
    minimum: float | None = None,
    maximum: float | None = None,
    *,
    minimum_inclusive: bool = True,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None:
        below = number < minimum if minimum_inclusive else number <= minimum
        if below:
            operator = ">=" if minimum_inclusive else ">"
            raise ValueError(f"{name} must be {operator} {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return number


def _finite_positive(name: str, value: float) -> float:
    return _finite_in_range(name, value, minimum=0.0, minimum_inclusive=False)


def _finite_non_negative(name: str, value: float) -> float:
    return _finite_in_range(name, value, minimum=0.0)


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
    path = resolve_entry_config(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    identity = data["identity"]
    runtime = data["runtime"]
    brain = data["brain"]
    executive = dict(data.get("executive") or {})
    gpu_budget = runtime.get(
        "weekly_gpu_budget_hours",
        runtime.get("weekly_brain_budget_hours", 30),
    )

    state_dir_raw = os.getenv("ELIA_STATE_DIR", str(runtime["state_dir"]))
    state_dir = _resolve_project_path(path, state_dir_raw)
    auto_checkpoint_raw = os.getenv(
        "ELIA_AUTO_CHECKPOINT_PATH",
        str(runtime.get("auto_checkpoint_path", "")).strip(),
    ).strip()
    auto_checkpoint_path = (
        _resolve_project_path(path, auto_checkpoint_raw)
        if auto_checkpoint_raw
        else None
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
    epistemic_raw = os.getenv(
        "ELIA_EPISTEMIC_CONFIG",
        str(data.get("epistemic_registry", "epistemic.yaml")),
    )
    skills_raw = os.getenv("ELIA_SKILLS_DIR", str(data.get("skills_dir", "skills")))
    model_revision_raw = os.getenv(
        "ELIA_MODEL_REVISION",
        str(brain.get("model_revision", "")).strip(),
    ).strip()

    executive_defaults = ExecutiveConfig()
    executive_config = ExecutiveConfig(
        enabled=_env_bool(
            "ELIA_EXECUTIVE_ENABLED",
            bool(executive.get("enabled", executive_defaults.enabled)),
        ),
        critical_need_threshold=float(
            executive.get(
                "critical_need_threshold",
                executive_defaults.critical_need_threshold,
            )
        ),
        maintenance_need_threshold=float(
            executive.get(
                "maintenance_need_threshold",
                executive_defaults.maintenance_need_threshold,
            )
        ),
        low_budget_ratio=float(
            executive.get("low_budget_ratio", executive_defaults.low_budget_ratio)
        ),
        deep_budget_ratio=float(
            executive.get("deep_budget_ratio", executive_defaults.deep_budget_ratio)
        ),
        deep_focus_threshold=float(
            executive.get(
                "deep_focus_threshold",
                executive_defaults.deep_focus_threshold,
            )
        ),
        low_tokens=int(executive.get("low_tokens", executive_defaults.low_tokens)),
        normal_tokens=int(
            executive.get("normal_tokens", executive_defaults.normal_tokens)
        ),
        deep_tokens=int(executive.get("deep_tokens", executive_defaults.deep_tokens)),
        low_target_brain_seconds=float(
            executive.get(
                "low_target_brain_seconds",
                executive_defaults.low_target_brain_seconds,
            )
        ),
        normal_target_brain_seconds=float(
            executive.get(
                "normal_target_brain_seconds",
                executive_defaults.normal_target_brain_seconds,
            )
        ),
        deep_target_brain_seconds=float(
            executive.get(
                "deep_target_brain_seconds",
                executive_defaults.deep_target_brain_seconds,
            )
        ),
        halt_sleep_seconds=float(
            executive.get(
                "halt_sleep_seconds",
                executive_defaults.halt_sleep_seconds,
            )
        ),
        exhausted_sleep_seconds=float(
            executive.get(
                "exhausted_sleep_seconds",
                executive_defaults.exhausted_sleep_seconds,
            )
        ),
        conserve_sleep_seconds=float(
            executive.get(
                "conserve_sleep_seconds",
                executive_defaults.conserve_sleep_seconds,
            )
        ),
        idle_sleep_seconds=float(
            executive.get("idle_sleep_seconds", executive_defaults.idle_sleep_seconds)
        ),
        adaptive_thinking=_env_bool(
            "ELIA_EXECUTIVE_ADAPTIVE_THINKING",
            bool(
                executive.get(
                    "adaptive_thinking",
                    executive_defaults.adaptive_thinking,
                )
            ),
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
            timeout_seconds=float(
                os.getenv("ELIA_MODEL_TIMEOUT", brain["timeout_seconds"])
            ),
            max_tokens=int(os.getenv("ELIA_MAX_TOKENS", brain["max_tokens"])),
            temperature=float(os.getenv("ELIA_TEMPERATURE", brain["temperature"])),
            top_p=float(os.getenv("ELIA_TOP_P", brain["top_p"])),
            thinking=_env_bool(
                "ELIA_THINKING",
                bool(brain.get("thinking", False)),
            ),
            model_revision=model_revision_raw or None,
        ),
        runtime=RuntimeConfig(
            state_dir=state_dir,
            cycle_sleep_seconds=float(
                os.getenv(
                    "ELIA_CYCLE_SLEEP_SECONDS",
                    runtime["cycle_sleep_seconds"],
                )
            ),
            max_action_output_chars=int(runtime["max_action_output_chars"]),
            weekly_gpu_budget_hours=float(
                os.getenv("ELIA_WEEKLY_GPU_HOURS", gpu_budget)
            ),
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
        epistemic_path=_resolve_config_path(path, epistemic_raw),
        skills_dir=_resolve_project_path(path, skills_raw),
        branch_id=branch_id,
        executive=executive_config,
    )
