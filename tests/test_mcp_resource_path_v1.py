from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mcp")

from elia.mcp_server import build_mcp_server


def test_mcp_default_config_resolution_is_not_bound_to_cwd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    server = build_mcp_server()
    assert server is not None
