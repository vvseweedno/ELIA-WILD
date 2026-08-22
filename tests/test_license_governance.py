from __future__ import annotations

import tomllib
from pathlib import Path

from elia import __version__


ROOT = Path(__file__).resolve().parents[1]
LICENSE_EXPRESSION = "LicenseRef-ELIA-WILD-Proprietary-1.0"


def test_proprietary_license_metadata_cannot_drift_silently() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert project["license"] == LICENSE_EXPRESSION
    assert project["license-files"] == ["LICENSE", "COMMERCIAL_LICENSING.md"]
    assert project["version"] == __version__


def test_public_installation_does_not_grant_free_use() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    normalized = " ".join(license_text.split())

    assert "NOT OPEN-SOURCE OR FREE-TO-USE SOFTWARE" in normalized
    assert "PAID LICENSE REQUIRED FOR USE" in normalized
    assert "modify, adapt, translate, patch" in normalized
    assert "Copyright (c) 2026 vvseweedno. All rights reserved." in normalized


def test_canonical_tree_is_owned_by_repository_owner() -> None:
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")

    assert codeowners.splitlines()[-1] == "* @vvseweedno"
