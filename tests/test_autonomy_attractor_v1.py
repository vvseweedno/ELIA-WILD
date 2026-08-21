from __future__ import annotations

from pathlib import Path

from elia.attractor import AutonomyAttractor


def make_attractor(tmp_path: Path, text: str = "# test attractor\ncontinue verified work") -> AutonomyAttractor:
    path = tmp_path / "autonomy_attractor.md"
    path.write_text(text, encoding="utf-8")
    return AutonomyAttractor.load(path)


def catalog() -> dict[str, dict[str, object]]:
    return {
        "stage_deliverable": {
            "enabled": True,
            "authority": "workspace_write",
            "side_effects": "writes staged local artifact",
            "cost_class": "low",
        },
        "http_get": {
            "enabled": True,
            "authority": "public_network_read",
            "side_effects": "remote read request",
            "cost_class": "network",
        },
        "self_check": {
            "enabled": True,
            "authority": "local_self_diagnostic",
            "side_effects": "temporary local scratch only",
            "cost_class": "low",
        },
        "submit_work": {
            "enabled": True,
            "authority": "configured_external_work",
            "side_effects": "submits an already staged deliverable",
            "cost_class": "network",
        },
    }


def prediction(info: float = 0.2, success: float = 0.9) -> dict[str, float]:
    return {
        "expected_information_gain": info,
        "action_success_probability": success,
    }


def test_attractor_prefers_advancing_persisted_planned_work_over_novel_read(tmp_path: Path) -> None:
    attractor = make_attractor(tmp_path)
    agency = {
        "continuation_work_item": {
            "id": 7,
            "opportunity_id": 3,
            "status": "planned",
            "objective": "finish the already accepted work plan",
        }
    }

    continuation = attractor.evaluate(
        action_name="stage_deliverable",
        prediction=prediction(0.2),
        agency=agency,
        capability_catalog=catalog(),
        assurance_accepted=True,
    )
    novelty = attractor.evaluate(
        action_name="http_get",
        prediction=prediction(0.2),
        agency=agency,
        capability_catalog=catalog(),
        assurance_accepted=True,
    )

    assert continuation.score is not None
    assert novelty.score is not None
    assert continuation.commitment == 1.0
    assert continuation.score > novelty.score


def test_hard_constraint_failure_has_no_tradeable_soft_score(tmp_path: Path) -> None:
    attractor = make_attractor(tmp_path)
    result = attractor.evaluate(
        action_name="submit_work",
        prediction=prediction(1.0, 1.0),
        agency={},
        capability_catalog=catalog(),
        assurance_accepted=False,
    )

    assert result.hard_constraints_satisfied is False
    assert result.score is None
    assert any("soft utility is not applicable" in note for note in result.notes)


def test_critical_continuity_pressure_beats_external_submission(tmp_path: Path) -> None:
    attractor = make_attractor(tmp_path)
    agency = {
        "selected_need": {
            "name": "continuity_integrity",
            "severity": 1.0,
        }
    }

    diagnostic = attractor.evaluate(
        action_name="self_check",
        prediction=prediction(0.4),
        agency=agency,
        capability_catalog=catalog(),
        assurance_accepted=True,
    )
    external = attractor.evaluate(
        action_name="submit_work",
        prediction=prediction(0.4),
        agency=agency,
        capability_catalog=catalog(),
        assurance_accepted=True,
    )

    assert diagnostic.score is not None and external.score is not None
    assert diagnostic.continuity == 1.0
    assert diagnostic.score > external.score


def test_attractor_fingerprint_changes_with_contract_text(tmp_path: Path) -> None:
    first = make_attractor(tmp_path, "first attractor")
    second = make_attractor(tmp_path, "second attractor")
    assert first.fingerprint != second.fingerprint
