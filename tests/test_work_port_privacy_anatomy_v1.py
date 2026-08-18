from __future__ import annotations

import json

from elia.organism import OrganismManifest
from elia.provider_context import provider_context


def test_provider_context_never_exports_submission_reference_or_response_fingerprint() -> None:
    context = {
        "work_ports": {
            "enabled": True,
            "readiness": "ready",
            "ports": {
                "marketplace": {
                    "server": "market",
                    "submit_tool": "submit",
                    "outcome_tool": "status",
                }
            },
            "active_submissions": [
                {
                    "id": 3,
                    "work_item_id": 11,
                    "port_name": "marketplace",
                    "submitted_at": "2026-08-18T00:00:00+00:00",
                    "updated_at": "2026-08-18T00:01:00+00:00",
                    "submission_observation_id": 44,
                    "submission_ref": "PRIVATE_REMOTE_SUBMISSION_REFERENCE",
                    "remote_status": "submitted",
                    "last_outcome_observation_id": None,
                    "response_fingerprint": "PRIVATE_RESPONSE_FINGERPRINT",
                }
            ],
        }
    }
    public = provider_context(context)
    serialized = json.dumps(public, sort_keys=True)
    assert "PRIVATE_REMOTE_SUBMISSION_REFERENCE" not in serialized
    assert "PRIVATE_RESPONSE_FINGERPRINT" not in serialized
    item = public["work_ports"]["active_submissions"][0]
    assert item["work_item_id"] == 11
    assert item["remote_status"] == "submitted"
    assert "submission_ref" not in item
    assert "response_fingerprint" not in item


def test_genesis_1_5_anatomy_overlay_is_required_and_auditable() -> None:
    manifest = OrganismManifest.load()
    assert manifest.schema_version >= 7
    overlays = {item["name"] for item in manifest.raw.get("anatomy_overlays", [])}
    assert "1.5-external-work-ports.yaml" in overlays
    organs = {item.id: item for item in manifest.organs}
    for organ_id in ("work_port_store", "work_port_registry", "external_work_runtime"):
        assert organ_id in organs
        assert organs[organ_id].required is True
        assert organs[organ_id].maturity == "prototype"
    report = manifest.audit(expected_identity_id="elia-wild")
    assert report.healthy is True
    statuses = {item.organ.id: item for item in report.statuses}
    assert statuses["work_port_store"].available is True
    assert statuses["work_port_registry"].available is True
    assert statuses["external_work_runtime"].available is True
