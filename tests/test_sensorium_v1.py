from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from elia.observations import ObservationStore, _canonical_json, _text_digest
from elia.tools import ToolRegistry


def test_tool_execution_automatically_becomes_observation_and_experience(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path / "workspace")
    result = registry.execute("write_workspace", {"path": "a.txt", "content": "hello"})
    assert result.ok is True

    observations = registry.observations.recent(8)
    assert observations[0].source_ref == "write_workspace"
    assert observations[0].success is True
    assert observations[0].transaction_id

    experiences = registry.causal.recent(8)
    assert experiences[0].action_name == "write_workspace"
    assert experiences[0].observation_id == observations[0].id
    valid, error = registry.state_bus.verify(observations[0].transaction_id or "")
    assert valid is True, error


def test_sensorium_payload_is_bounded_but_full_digest_is_stable(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "memory.sqlite3")
    huge = {"text": "x" * 700_000}
    observation = store.record(
        source_kind="test",
        source_ref="oversized",
        payload=huge,
    )
    assert observation.payload["_truncated"] is True
    assert observation.payload["original_sha256"] == observation.payload_sha256
    assert observation.payload["original_bytes"] > 512_000


def test_world_model_tools_never_self_verify(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path / "workspace")
    created = registry.execute(
        "world_model_propose",
        {
            "domain": "test",
            "subject": "thing",
            "predicate": "state",
            "object": "present",
            "confidence": 1.0,
            "evidence": "observation says present",
        },
    )
    assert created.ok is True
    assert created.data["status"] == "hypothesis"
    assert created.data["confidence"] == 0.75

    rejected = registry.execute(
        "world_model_revise",
        {
            "id": created.data["id"],
            "status": "verified",
            "confidence": 1.0,
            "evidence": "model assertion",
        },
    )
    assert rejected.ok is False
    assert "hypothesis/supported/disputed" in (rejected.error or "")


def test_sensitive_observation_persists_only_scrubbed_projection(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    store = ObservationStore(database)
    token = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    email = "owner.private@example.org"
    phone = "+1 202 555 0123"
    arbitrary_private = "UNCLASSIFIED_PRIVATE_CUSTOMER_NOTE_9e20"

    class SensitiveRepr:
        def __repr__(self) -> str:
            return token + arbitrary_private

    observation = store.record(
        source_kind="body",
        source_ref="http_get",
        payload={
            "ok": True,
            "tool": "http_get",
            "data": {"text": f"Authorization: Bearer {token}; {email}; {phone}"},
        },
        summary=arbitrary_private,
        provenance={
            "contact_email": email,
            "authorization": f"Bearer {token}",
            "debug_payload": arbitrary_private,
            "custom": SensitiveRepr(),
            "authority": "configured_body",
        },
    )

    assert observation.data_classification == "sensitive"
    assert observation.payload["_persistence_redacted"] is True
    assert observation.payload["projection"]["tool"] == "http_get"
    assert "data_fingerprint" in observation.payload["projection"]
    durable = b"".join(
        path.read_bytes() for path in tmp_path.glob("memory.sqlite3*") if path.is_file()
    )
    for private_value in (token, email, phone, arbitrary_private):
        assert private_value.encode("utf-8") not in durable
    assert "SUMMARY REDACTED" in observation.summary
    assert observation.provenance["authority"] == "configured_body"
    assert "debug_payload" not in observation.provenance


def test_observation_rejects_nonfinite_trust_and_canonicalizes_nonfinite_payload(
    tmp_path: Path,
) -> None:
    store = ObservationStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match="trust must be finite"):
        store.record(source_kind="test", source_ref="bad-trust", payload={}, trust=float("nan"))

    observation = store.record(
        source_kind="test",
        source_ref="nonfinite-payload",
        payload={"value": float("inf")},
    )
    assert observation.payload["value"] == {
        "type": "non_finite_number",
        "value": "inf",
    }


def _legacy_observation_schema(database: Path) -> None:
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                transaction_id TEXT NULL,
                source_kind TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                modality TEXT NOT NULL,
                content_type TEXT NOT NULL,
                trust REAL NOT NULL,
                success INTEGER NOT NULL,
                summary TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                stored_payload_sha256 TEXT NOT NULL DEFAULT '',
                is_compacted INTEGER NOT NULL DEFAULT 0,
                provenance_json TEXT NOT NULL
            )
            """
        )


def test_legacy_sensitive_rows_are_projected_during_schema_migration(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_observation_schema(database)
    token = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    email = "legacy.private@example.org"
    payload = {"ok": True, "tool": "http_get", "data": {"text": token + email}}
    raw = _canonical_json(payload)
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            INSERT INTO observations(
                observed_at, transaction_id, source_kind, source_ref, modality,
                content_type, trust, success, summary, payload_json, payload_sha256,
                stored_payload_sha256, is_compacted, provenance_json
            ) VALUES ('now', NULL, 'body', 'http_get', 'structured',
                      'application/json', 0.8, 1, ?, ?, ?, ?, 0, ?)
            """,
            (
                f"contact={email} token={token}",
                raw,
                _text_digest(raw),
                _text_digest(raw),
                _canonical_json({"authorization": token, "contact_email": email}),
            ),
        )

    store = ObservationStore(database)
    migrated = store.get(1)
    assert migrated is not None
    assert migrated.data_classification == "sensitive"
    assert migrated.payload["_persistence_redacted"] is True
    with sqlite3.connect(database) as conn:
        row = conn.execute(
            "SELECT summary, payload_json, provenance_json, payload_sha256, is_redacted "
            "FROM observations WHERE id=1"
        ).fetchone()
    assert row is not None
    serialized = " ".join(str(value) for value in row)
    assert token not in serialized
    assert email not in serialized
    assert row[3] == _text_digest(raw)
    assert row[4] == 1
    assert store.migrate_legacy_sensitive_payloads() == 0


def test_malformed_legacy_sensitive_row_fails_migration_closed(tmp_path: Path) -> None:
    database = tmp_path / "malformed.sqlite3"
    _legacy_observation_schema(database)
    malformed = "{not-json"
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            INSERT INTO observations(
                observed_at, transaction_id, source_kind, source_ref, modality,
                content_type, trust, success, summary, payload_json, payload_sha256,
                stored_payload_sha256, is_compacted, provenance_json
            ) VALUES ('now', NULL, 'body', 'http_get', 'structured',
                      'application/json', 0.8, 0, 'bad', ?, ?, ?, 0, '{}')
            """,
            (malformed, "0" * 64, _text_digest(malformed)),
        )

    with pytest.raises(RuntimeError, match="is malformed"):
        ObservationStore(database)
