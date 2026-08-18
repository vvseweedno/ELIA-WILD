from __future__ import annotations

import os
from pathlib import Path
import sys


DEFAULT_CONFIG_RELATIVE = Path("config/genesis.yaml")


def checkout_root() -> Path:
    return Path(__file__).resolve().parents[1]


def installed_data_root() -> Path:
    override = os.getenv("ELIA_DATA_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path(sys.prefix) / "share" / "elia-wild").resolve()


def data_root() -> Path:
    root = checkout_root()
    if (root / DEFAULT_CONFIG_RELATIVE).is_file():
        return root
    return installed_data_root()


def resolve_entry_config(path: str | Path = DEFAULT_CONFIG_RELATIVE) -> Path:
    raw = Path(path).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    direct = raw.resolve()
    if direct.is_file():
        return direct
    if raw == DEFAULT_CONFIG_RELATIVE:
        packaged = data_root() / DEFAULT_CONFIG_RELATIVE
        if packaged.is_file():
            return packaged.resolve()
    return direct


def default_manifest_path() -> Path:
    return (data_root() / "config" / "organism.yaml").resolve()
