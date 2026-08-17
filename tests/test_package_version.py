from __future__ import annotations

from importlib.metadata import version

import elia


def test_runtime_and_package_versions_match() -> None:
    assert elia.__version__ == version("elia-wild")
