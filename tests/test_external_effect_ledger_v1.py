from __future__ import annotations

from pathlib import Path

import pytest

from elia.external_effects import ExternalEffectIndeterminate, ExternalEffectLedger


def test_external_effect_requires_reconciliation_after_failed_send(tmp_path: Path) -> None:
    ledger = ExternalEffectLedger(tmp_path / "memory.sqlite3")
    args = {"server": "work", "tool": "submit", "payload": {"id": 7}}

    intent = ledger.prepare("mcp_call", args)
    ledger.mark_sending(intent.effect_id)
    failed = ledger.record_result(
        intent.effect_id,
        ok=False,
        result={"error": "timeout"},
    )

    assert failed.status == "indeterminate"
    with pytest.raises(ExternalEffectIndeterminate):
        ledger.prepare("mcp_call", args)

    reconciled = ledger.reconcile(
        intent.effect_id,
        remote_effect_observed=False,
        evidence="Provider lookup by immutable request identity returned no matching effect.",
    )
    assert reconciled.status == "reconciled_no_effect"

    retry = ledger.prepare("mcp_call", args)
    assert retry.effect_id != intent.effect_id


def test_process_death_marks_sending_effect_indeterminate(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    first = ExternalEffectLedger(database)
    intent = first.prepare("browser_click", {"selector": "button[type=submit]"})
    first.mark_sending(intent.effect_id)

    second = ExternalEffectLedger(database)
    unresolved = second.recover_interrupted()

    restored = second.get(intent.effect_id)
    assert restored is not None
    assert restored.status == "indeterminate"
    assert any(item.effect_id == intent.effect_id for item in unresolved)


def test_proven_local_suppression_closes_effect_without_ambiguity(tmp_path: Path) -> None:
    ledger = ExternalEffectLedger(tmp_path / "memory.sqlite3")
    intent = ledger.prepare("process_run", {"executable": "/bin/echo"})
    ledger.mark_sending(intent.effect_id)

    closed = ledger.record_result(
        intent.effect_id,
        ok=False,
        result={"suppressed": True},
        no_effect_proven=True,
    )

    assert closed.status == "reconciled_no_effect"
    assert ledger.diagnostics()["unresolved_count"] == 0


def test_successful_external_effect_is_terminal_and_new_intent_is_distinct(tmp_path: Path) -> None:
    ledger = ExternalEffectLedger(tmp_path / "memory.sqlite3")
    args = {"endpoint": "configured", "method": "send"}

    first = ledger.prepare("jsonrpc_call", args)
    ledger.mark_sending(first.effect_id)
    done = ledger.record_result(first.effect_id, ok=True, result={"ok": True})
    assert done.status == "succeeded"

    second = ledger.prepare("jsonrpc_call", args)
    assert second.effect_id != first.effect_id
    assert second.idempotency_key != first.idempotency_key


def test_external_effect_argument_fingerprint_is_canonical_by_key_order(
    tmp_path: Path,
) -> None:
    ledger = ExternalEffectLedger(tmp_path / "memory.sqlite3")
    first = ledger.prepare("mcp_call", {"server": "x", "arguments": {"a": 1, "b": 2}})
    second = ledger.prepare("mcp_call", {"arguments": {"b": 2, "a": 1}, "server": "x"})
    assert second.effect_id == first.effect_id
    assert second.arguments_sha256 == first.arguments_sha256


@pytest.mark.parametrize("invalid", [{"x": float("nan")}, {"x": object()}])
def test_external_effect_rejects_noncanonical_arguments_before_intent(
    tmp_path: Path,
    invalid,
) -> None:
    ledger = ExternalEffectLedger(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError):
        ledger.prepare("mcp_call", invalid)
    assert ledger.recent() == []
