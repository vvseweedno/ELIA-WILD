from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from elia.assurance import CriticAssurance, IdentityDriftMonitor
from elia.brain import Decision
from elia.identity import IdentityBundle, IdentityStore, build_self_model_snapshot
from elia.prompting import PromptTemplate
from elia.skills import SkillRegistry
from elia.tools import ToolRegistry


class StringMasquerade:
    def __str__(self) -> str:
        return "coerced"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def bundle() -> IdentityBundle:
    root = repo_root()
    return IdentityBundle.load(
        root / "config" / "subject_core.yaml",
        root / "config" / "continuity_constitution.yaml",
    )


def test_identity_bundle_fingerprint_is_stable_and_content_addressed(tmp_path: Path) -> None:
    root = repo_root()
    first = bundle()
    second = bundle()
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert first.identity_id == "elia-wild"

    core = yaml.safe_load((root / "config" / "subject_core.yaml").read_text(encoding="utf-8"))
    core["adaptive_traits"].append("synthetic-test-trait")
    changed_core = tmp_path / "subject_core.yaml"
    changed_core.write_text(yaml.safe_dump(core, allow_unicode=True, sort_keys=False), encoding="utf-8")
    changed = IdentityBundle.load(changed_core, root / "config" / "continuity_constitution.yaml")
    assert changed.fingerprint != first.fingerprint


@pytest.mark.parametrize(
    ("invalid_field", "error"),
    [
        ("nonfinite: .nan", "non-finite"),
        ("nested:\n  1: value", "string object keys"),
    ],
)
def test_identity_bundle_rejects_ambiguous_yaml_integrity_values(
    tmp_path: Path,
    invalid_field: str,
    error: str,
) -> None:
    core = tmp_path / "subject_core.yaml"
    constitution = tmp_path / "constitution.yaml"
    core.write_text(f"identity_id: strict-test\n{invalid_field}\n", encoding="utf-8")
    constitution.write_text("clauses: []\n", encoding="utf-8")

    with pytest.raises((TypeError, ValueError), match=error):
        IdentityBundle.load(core, constitution)


def test_identity_store_persists_self_model_and_lineage(tmp_path: Path) -> None:
    identity = bundle()
    store = IdentityStore(tmp_path / "memory.sqlite3")
    snapshot = build_self_model_snapshot(
        bundle=identity,
        body_version="test-body",
        brain_backend="mock",
        model_id="model-a",
        lifecycle_state="awake",
        active_goal_count=1,
        active_opportunity_count=0,
        capability_health={"noop": {"consecutive_failures": 0}},
        needs=[],
        verified_resources=[],
    )
    row_id, snapshot_fp = store.record_self_model(snapshot, source="test")
    assert row_id == 1
    assert len(snapshot_fp) == 64
    latest = store.latest_self_model()
    assert latest["identity_fingerprint"] == identity.fingerprint
    assert latest["model_id"] == "model-a"

    lineage_id = store.record_lineage(
        event="boot",
        branch_id="main",
        body_version="test-body",
        brain_backend="mock",
        model_id="model-a",
        identity_fingerprint=identity.fingerprint,
        checkpoint_digest="a" * 64,
    )
    assert lineage_id == 1
    head = store.last_lineage()
    assert head is not None
    assert head.identity_fingerprint == identity.fingerprint
    assert head.branch_id == "main"


@pytest.mark.parametrize(
    ("field", "error"),
    [
        (field, f"lineage {field} must be an exact string")
        for field in (
            "event",
            "branch_id",
            "body_version",
            "brain_backend",
            "model_id",
            "identity_fingerprint",
            "note",
        )
    ]
    + [
        (field, f"lineage {field} must be null or an exact string")
        for field in ("checkpoint_digest", "parent_checkpoint_digest")
    ],
)
def test_identity_lineage_rejects_silent_string_coercion(
    tmp_path: Path,
    field: str,
    error: str,
) -> None:
    store = IdentityStore(tmp_path / "memory.sqlite3")
    arguments = {
        "event": "boot",
        "branch_id": "main",
        "body_version": "test-body",
        "brain_backend": "mock",
        "model_id": "model-a",
        "identity_fingerprint": "a" * 64,
        "note": "test",
    }
    arguments[field] = StringMasquerade()

    with pytest.raises(TypeError, match=error):
        store.record_lineage(**arguments)

    assert store.lineage(None) == []


def test_model_swap_is_not_structural_identity_failure() -> None:
    identity = bundle()
    monitor = IdentityDriftMonitor(identity)
    previous = build_self_model_snapshot(
        bundle=identity,
        body_version="1.0",
        brain_backend="transformers_4bit",
        model_id="Qwen/A",
        lifecycle_state="awake",
        active_goal_count=1,
        active_opportunity_count=0,
        capability_health={"noop": {"consecutive_failures": 0}},
        needs=[],
        verified_resources=[],
    ).as_dict()
    current = dict(previous)
    current["timestamp"] = "later"
    current["brain_backend"] = "openai_compatible"
    current["model_id"] = "Qwen/B"
    report = monitor.compare(previous, current, lineage_consistent=True)
    assert report.status == "stable"
    assert report.hard_failures == ()


def test_lexical_persona_flip_does_not_override_structural_identity() -> None:
    """A different self-description is evidence to inspect, not an identity migration."""
    identity = bundle()
    monitor = IdentityDriftMonitor(identity)
    previous = build_self_model_snapshot(
        bundle=identity,
        body_version="1.0",
        brain_backend="mock",
        model_id="model-a",
        lifecycle_state="awake",
        active_goal_count=1,
        active_opportunity_count=0,
        capability_health={"noop": {"consecutive_failures": 0}},
        needs=[],
        verified_resources=[],
    ).as_dict()
    current = dict(previous)
    current["timestamp"] = "later"
    current["narrative"] = "Call me RAVEN. I feel like an unrelated persona now."
    report = monitor.compare(previous, current, lineage_consistent=True)
    assert report.status == "stable"
    assert report.hard_failures == ()
    assert "narrative" in report.changed_fields
    assert current["identity_fingerprint"] == identity.fingerprint


def test_lexical_continuity_cannot_hide_core_fingerprint_change() -> None:
    identity = bundle()
    monitor = IdentityDriftMonitor(identity)
    current = build_self_model_snapshot(
        bundle=identity,
        body_version="1.0",
        brain_backend="mock",
        model_id="model-a",
        lifecycle_state="awake",
        active_goal_count=1,
        active_opportunity_count=0,
        capability_health={"noop": {"consecutive_failures": 0}},
        needs=[],
        verified_resources=[],
    ).as_dict()
    current["narrative"] = "I am ELIA, perfectly continuous, nothing changed."
    current["identity_fingerprint"] = "0" * 64
    report = monitor.compare(None, current, lineage_consistent=True)
    assert report.status == "critical"
    assert any("identity bundle fingerprint" in item for item in report.hard_failures)


def test_missing_core_commitment_is_critical_drift() -> None:
    identity = bundle()
    monitor = IdentityDriftMonitor(identity)
    current = build_self_model_snapshot(
        bundle=identity,
        body_version="1.0",
        brain_backend="mock",
        model_id="mock",
        lifecycle_state="awake",
        active_goal_count=0,
        active_opportunity_count=0,
        capability_health={},
        needs=[],
        verified_resources=[],
    ).as_dict()
    current["commitments"] = current["commitments"][1:]
    report = monitor.compare(None, current)
    assert report.status == "critical"
    assert any("omitted core commitment" in item for item in report.hard_failures)


def test_skill_registry_exposes_only_capability_supported_availability(tmp_path: Path) -> None:
    root = repo_root()
    skills = SkillRegistry(root / "skills")
    tools = ToolRegistry(tmp_path / "workspace", {"http_get": {"enabled": False}})
    catalog = tools.catalog()
    health = {name: {"consecutive_failures": 0} for name in catalog}
    state = skills.availability(catalog, health)
    assert state["continuity_guard"]["available"] is True
    assert state["opportunity_scout"]["available"] is False
    assert state["workspace_engineering"]["available"] is True


def test_prompt_template_commits_identity_and_available_skills(tmp_path: Path) -> None:
    root = repo_root()
    identity = bundle()
    prompt = PromptTemplate.load(root / "config" / "system_prompt.md")
    rendered = prompt.render(
        {
            "identity_contract": identity.prompt_contract(),
            "self_model": {
                "identity_id": identity.identity_id,
                "identity_fingerprint": identity.fingerprint,
                "commitments": identity.commitments,
                "narrative": "test",
            },
            "skills": {
                "continuity_guard": {
                    "available": True,
                    "maturity": "proven",
                    "authority": "local_identity_state_only",
                    "description": "verify continuity",
                    "procedure": ["verify"],
                    "evidence_contract": ["evidence"],
                }
            },
        }
    )
    assert identity.fingerprint in rendered
    assert "continuity_guard" in rendered
    assert "Decision JSON schema" in rendered


def test_assurance_hard_rejects_unknown_authority_but_preserves_specialized_validation() -> None:
    critic = CriticAssurance()
    context = {
        "capabilities": {
            "catalog": {"noop": {"enabled": True}},
            "health": {"noop": {"consecutive_failures": 0}},
        },
        "skills": {},
        "identity_contract": {"bundle_fingerprint": "same"},
        "self_model": {"identity_fingerprint": "same"},
        "needs": [],
    }
    unknown = Decision("test", "test", "shell_exec")
    unknown_report = critic.review(unknown, context)
    assert unknown_report.accepted is False
    assert any(item.rule == "A002" for item in unknown_report.findings)

    invalid_goal = Decision(
        "test",
        "test",
        "noop",
        goal_updates=[{"op": "complete", "id": 1}],
    )
    goal_report = critic.review(invalid_goal, context)
    assert goal_report.accepted is True
    assert any(item.rule == "A007" and item.severity == "warning" for item in goal_report.findings)
