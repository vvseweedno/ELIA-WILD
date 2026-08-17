from __future__ import annotations

from pathlib import Path

from elia.identity import IdentityBundle
from elia.organism import OrganismManifest, default_manifest_path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_organism_manifest_audits_all_required_organs() -> None:
    manifest = OrganismManifest.load(default_manifest_path())
    identity = IdentityBundle.load(
        repo_root() / "config" / "subject_core.yaml",
        repo_root() / "config" / "continuity_constitution.yaml",
    )
    report = manifest.audit(expected_identity_id=identity.identity_id)
    assert report.healthy is True
    assert report.identity_id == identity.identity_id
    assert len(report.manifest_fingerprint) == 64
    assert len(report.architecture_fingerprint) == 64
    required = [item for item in report.statuses if item.organ.required]
    assert required
    assert all(item.available for item in required)


def test_research_organs_are_explicitly_non_core() -> None:
    manifest = OrganismManifest.load()
    research = [item for item in manifest.organs if item.layer == "research"]
    assert research
    assert all(item.required is False for item in research)
    assert all(item.maturity in {"prototype", "archived", "hypothesis"} for item in research)


def test_prompt_contract_separates_core_from_research() -> None:
    contract = OrganismManifest.load().prompt_contract()
    assert contract["identity_id"] == "elia-wild"
    assert any(item["id"] == "persistent_memory" for item in contract["core_organs"])
    assert "prototype" in contract["research_maturity"]
    assert "not identity authority" in contract["research_rule"]
