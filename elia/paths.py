from __future__ import annotations

from pathlib import Path
import os
import sys


PROJECT_NAME = "elia-wild"
DEFAULT_CONFIG_RELATIVE = Path("config/genesis.yaml")


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def source_root() -> Path:
    return package_dir().parent


def source_resource_root() -> Path:
    return source_root()


def installed_resource_root() -> Path:
    override = os.getenv("ELIA_RESOURCE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path(sys.prefix).resolve() / "share" / PROJECT_NAME)


def default_resource_root() -> Path:
    """Return the resource root for the current installation.

    Source/editable checkouts keep one canonical `config/` + `skills/` tree at the
    repository root. Built wheels install those exact files under
    `<sys.prefix>/share/elia-wild/`. No runtime logic depends on the process cwd to
    locate immutable identity/configuration resources.
    """

    source = source_resource_root()
    if (source / "config" / "genesis.yaml").is_file():
        return source
    installed = installed_resource_root()
    if (installed / "config" / "genesis.yaml").is_file():
        return installed
    # Return the deterministic installed location so error messages identify the
    # expected path instead of silently selecting an unrelated cwd file.
    return installed


def default_config_dir() -> Path:
    return default_resource_root() / "config"


def default_genesis_path() -> Path:
    return default_config_dir() / "genesis.yaml"


def default_skills_dir() -> Path:
    return default_resource_root() / "skills"


def resolve_config_entry(path: str | Path | None) -> Path:
    """Resolve a user config path with a wheel-safe canonical default.

    An explicitly existing relative or absolute file always wins. The historical
    string `config/genesis.yaml` is treated as the canonical default when it does not
    exist in cwd, preserving all public CLI entrypoints while making wheel installs
    independent from repository checkout layout.
    """

    if path is None:
        return default_genesis_path().resolve()
    raw = Path(path).expanduser()
    if raw.is_file():
        return raw.resolve()
    if not raw.is_absolute() and raw == DEFAULT_CONFIG_RELATIVE:
        return default_genesis_path().resolve()
    return raw.resolve()


def is_installed_resource(path: Path) -> bool:
    candidate = Path(path).expanduser().resolve()
    installed = installed_resource_root().resolve()
    try:
        candidate.relative_to(installed)
        return True
    except ValueError:
        return False


def mutable_runtime_root(config_path: Path) -> Path:
    """Choose where relative mutable state lives.

    Repository/external configs retain the historical project-root semantics.
    Package-owned wheel resources are read-only installation assets, so relative
    runtime state belongs to the operator's current working directory instead.
    `ELIA_RUNTIME_ROOT` may explicitly override either behavior.
    """

    override = os.getenv("ELIA_RUNTIME_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    config_path = Path(config_path).resolve()
    if is_installed_resource(config_path):
        return Path.cwd().resolve()
    return config_path.parent.parent.resolve()
