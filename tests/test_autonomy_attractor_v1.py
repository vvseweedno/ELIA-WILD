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
        "body_diagnostics": {
            "enabled": True,
            "authority": "local_body_read",
            "side_effects": "none",
            "cost_class": "low",
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
        authority_accepted=True,
    )
    novelty = attractor.evaluate(
        action_name="http_get",
        prediction=prediction(0.2),
        agency=agency,
        capability_catalog=catalog(),
        assurance_accepted=True,
        authority_accepted=True,
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
        authority_accepted=True,
    )
    external = attractor.evaluate(
        action_name="submit_work",
        prediction=prediction(0.4),
        agency=agency,
        capability_catalog=catalog(),
        assurance_accepted=True,
        authority_accepted=True,
    )

    assert diagnostic.score is not None and external.score is not None
    assert diagnostic.continuity == 1.0
    assert diagnostic.score > external.score


def test_body_readiness_rewards_diagnostics_not_unrelated_external_action(tmp_path: Path) -> None:
    attractor = make_attractor(tmp_path)
    agency = {
        "selected_need": {
            "name": "body_readiness",
            "severity": 0.55,
        }
    }

    diagnostic = attractor.evaluate(
        action_name="body_diagnostics",
        prediction=prediction(0.5),
        agency=agency,
        capability_catalog=catalog(),
        assurance_accepted=True,
        authority_accepted=True,
    )
    unrelated = attractor.evaluate(
        action_name="submit_work",
        prediction=prediction(0.5),
        agency=agency,
        capability_catalog=catalog(),
        assurance_accepted=True,
        authority_accepted=True,
    )

    assert diagnostic.score is not None and unrelated.score is not None
    assert diagnostic.commitment == 0.9
    assert diagnostic.continuity > unrelated.continuity
    assert diagnostic.score > unrelated.score


def test_attractor_fingerprint_changes_with_contract_text(tmp_path: Path) -> None:
    first = make_attractor(tmp_path, "first attractor")
    second = make_attractor(tmp_path, "second attractor")
    assert first.fingerprint != second.fingerprint


def test_pre_action_contract_ranks_only_explicitly_authorized_candidates(tmp_path: Path) -> None:
    attractor = make_attractor(tmp_path)
    ranked = attractor.evaluate_pre_action_candidates(
        [
            {
                "action_name": "http_get",
                "prediction": prediction(0.9),
                "assurance_accepted": True,
                # Missing authority must not default to acceptance.
            },
            {
                "action_name": "stage_deliverable",
                "prediction": prediction(0.2),
                "assurance_accepted": True,
                "authority_accepted": True,
            },
        ],
        agency={"continuation_work_item": {"id": 1, "status": "planned"}},
        capability_catalog=catalog(),
    )

    assert ranked[0].action_name == "stage_deliverable"
    assert ranked[0].pre_action_contract_satisfied is True
    assert ranked[1].score is None
    assert ranked[1].pre_action_contract_satisfied is False
