from __future__ import annotations

from pathlib import Path

import pytest

from elia.organism import OrganismManifest, default_manifest_path


def test_default_manifest_loads_latest_genesis_overlays() -> None:
    manifest = OrganismManifest.load()
    assert manifest.schema_version >= 8
    ids = {organ.id for organ in manifest.organs}
    assert {
        "resource_ecology_store",
        "resource_ecology_engine",
        "resource_runtime",
        "work_port_store",
        "work_port_registry",
        "external_work_runtime",
        "epistemic_registry",
        "cognitive_biographies",
        "evidence_view_projector",
        "epistemic_view_store",
        "resilient_epistemic_cortex",
        "epistemic_runtime",
    }.issubset(ids)
    overlay_names = {item["name"] for item in manifest.raw.get("anatomy_overlays", [])}
    assert "1.4-resource-ecology.yaml" in overlay_names
    assert "1.5-external-work-ports.yaml" in overlay_names
    assert "1.6-epistemic-ecosystem.yaml" in overlay_names
    report = manifest.audit(expected_identity_id="elia-wild")
    assert report.healthy is True
    required = {item.organ.id: item for item in report.statuses if item.organ.required}
    for organ_id in (
        "resource_ecology_store",
        "resource_ecology_engine",
        "resource_runtime",
        "work_port_store",
        "work_port_registry",
        "external_work_runtime",
        "epistemic_registry",
        "cognitive_biographies",
        "evidence_view_projector",
        "epistemic_view_store",
        "resilient_epistemic_cortex",
        "epistemic_runtime",
    ):
        assert required[organ_id].available is True


def test_custom_manifest_does_not_implicitly_absorb_project_overlays(tmp_path: Path) -> None:
    custom = tmp_path / "organism.yaml"
    custom.write_text(
        """schema_version: 1
identity_id: test-identity
name: Test
principle: isolated
layers:
  core: test
organs:
  - id: custom_artifact
    layer: core
    kind: artifact
    path: marker.txt
    required: true
    maturity: core
    authority: none
    role: custom
""",
        encoding="utf-8",
    )
    (tmp_path / "marker.txt").write_text("ok", encoding="utf-8")
    overlay_dir = tmp_path / "organism.d"
    overlay_dir.mkdir()
    (overlay_dir / "unexpected.yaml").write_text(
        """schema_version: 99
identity_id: test-identity
organs:
  - id: should_not_load
    layer: core
    kind: python
    module: does.not.exist
    required: true
    maturity: core
    authority: none
    role: no
""",
        encoding="utf-8",
    )
    manifest = OrganismManifest.load(custom)
    assert manifest.schema_version == 1
    assert {item.id for item in manifest.organs} == {"custom_artifact"}


def test_overlay_cannot_silently_replace_existing_organ(monkeypatch, tmp_path: Path) -> None:
    # This test exercises the merge rule directly through a temporary default layout.
    # Monkeypatching default_manifest_path keeps the production parser unchanged.
    import elia.organism as organism

    config = tmp_path / "config"
    config.mkdir()
    base = config / "organism.yaml"
    base.write_text(
        """schema_version: 1
identity_id: elia-wild
name: Test
principle: test
layers:
  core: core
organs:
  - id: persistent_memory
    layer: core
    kind: python
    module: elia.memory
    symbol: MemoryStore
    required: true
    maturity: core
    authority: local_state
    role: memory
""",
        encoding="utf-8",
    )
    overlay_dir = config / "organism.d"
    overlay_dir.mkdir()
    (overlay_dir / "duplicate.yaml").write_text(
        """schema_version: 2
identity_id: elia-wild
organs:
  - id: persistent_memory
    layer: core
    kind: python
    module: elia.memory
    symbol: MemoryStore
    required: true
    maturity: core
    authority: local_state
    role: replacement
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(organism, "default_manifest_path", lambda: base)
    with pytest.raises(ValueError, match="duplicate organ id"):
        OrganismManifest.load(base)
