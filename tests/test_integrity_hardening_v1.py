from __future__ import annotations

from pathlib import Path
import socket

import pytest

from elia.body.net import assert_http_url, resolve_http_target
from elia.chronicle import Chronicle
from elia.config import load_config
from elia.observations import ObservationStore


def _config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "genesis.yaml"


def test_relative_state_paths_are_canonical_to_project_not_cwd(monkeypatch, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ELIA_STATE_DIR", raising=False)
    monkeypatch.delenv("ELIA_AUTO_CHECKPOINT_PATH", raising=False)
    config = load_config(_config_path())
    assert config.runtime.state_dir == (repo_root / ".elia").resolve()
    assert config.runtime.state_dir.is_absolute()
    assert config.subject_core_path.is_absolute()
    assert config.brain.model_revision

    monkeypatch.setenv("ELIA_STATE_DIR", "state-from-env")
    config_from_env = load_config(_config_path())
    assert config_from_env.runtime.state_dir == (repo_root / "state-from-env").resolve()
    assert not config_from_env.runtime.state_dir.is_relative_to(tmp_path)


def test_chronicle_malformed_tail_is_integrity_failure_not_exception(tmp_path: Path) -> None:
    chronicle = Chronicle(tmp_path / "chronicle.jsonl")
    chronicle.append("GENESIS", {"ok": True})
    with chronicle.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq":2,"broken"')
    valid, error = chronicle.verify()
    assert valid is False
    assert error is not None
    assert "malformed entry" in error


def test_public_url_rejects_any_private_resolution(monkeypatch) -> None:
    def private_getaddrinfo(host, port, *, type):
        del host, type
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", private_getaddrinfo)
    with pytest.raises(ValueError, match="Non-public destination rejected"):
        assert_http_url("http://example.invalid/")


def test_public_url_resolution_deduplicates_valid_addresses(monkeypatch) -> None:
    def public_getaddrinfo(host, port, *, type):
        del host, type
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", public_getaddrinfo)
    host, port, addresses = resolve_http_target("https://example.invalid/path")
    assert host == "example.invalid"
    assert port == 443
    assert addresses == ["93.184.216.34"]


def test_sensorium_compacts_old_payload_but_preserves_digest(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "memory.sqlite3")
    first = store.record(source_kind="test", source_ref="one", payload={"secret": "A" * 2000})
    second = store.record(source_kind="test", source_ref="two", payload={"value": 2})
    third = store.record(source_kind="test", source_ref="three", payload={"value": 3})

    assert store.compact_aged_payloads(keep_recent=2, batch=10) == 1
    compacted = store.get(first.id)
    assert compacted is not None
    assert compacted.payload["_compacted"] is True
    assert compacted.payload["original_sha256"] == first.payload_sha256
    assert store.get(second.id).payload == {"value": 2}
    assert store.get(third.id).payload == {"value": 3}
