from __future__ import annotations

import json
from pathlib import Path

from elia.brain import _system_and_public_context
from elia.provider_context import provider_context
from elia.prompting import PromptTemplate


def test_provider_context_never_forwards_raw_sensor_payload() -> None:
    secret = "ELIA_SUPER_SECRET_TOKEN_123"
    context = {
        "_system_prompt": "system",
        "sensorium": [
            {
                "id": 7,
                "source_kind": "capability",
                "source_ref": "read_workspace",
                "success": True,
                "summary": "workspace read completed",
                "payload": {"content": secret, "nested": {"token": secret}},
                "payload_sha256": "a" * 64,
                "provenance": {"capability": "read_workspace", "secret": secret},
            }
        ],
        "world_model": {"beliefs": []},
    }

    system, public = _system_and_public_context(context)
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)

    assert system == "system"
    assert secret not in serialized
    assert "payload" not in public["sensorium"][0]
    assert public["sensorium"][0]["payload_sha256"] == "a" * 64
    assert "secret" not in public["sensorium"][0]["provenance"]


def test_provider_context_is_default_deny_for_unknown_top_level_state() -> None:
    public = provider_context(
        {
            "mission": "preserve continuity",
            "visible": {"x": 1},
            "future_sensitive_state": {"credential": "never-forward"},
            "_private": "never-forward",
        }
    )

    assert public == {"mission": "preserve continuity"}


def test_provider_context_scrubs_credentials_inside_allowed_fields() -> None:
    secret = "abcDEF1234567890"
    digest = "a" * 64
    public = provider_context(
        {
            "mission": f"diagnostic header Authorization: Bearer {secret}",
            "identity": {
                "callback": f"https://user:pass@example.org/report?access_token={secret}&view=1",
                "password": secret,
                "note": f"api_key={secret}",
                "payload_sha256": digest,
            },
            "world_model": {
                "beliefs": [{"id": 1, "object": secret, "evidence": secret}],
            },
        }
    )
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)

    assert secret not in serialized
    assert "user:pass@" not in serialized
    assert public["identity"]["password"] == "[REDACTED]"
    assert public["identity"]["payload_sha256"] == digest
    assert "object" not in public["world_model"]["beliefs"][0]
    assert "evidence" not in public["world_model"]["beliefs"][0]
    assert "[REDACTED]" in serialized


def test_provider_context_exposes_sanitized_durable_agency() -> None:
    local_path = "/home/private/.elia/workspace/deliverables/secret.txt"
    public = provider_context(
        {
            "agency": {
                "version": 1,
                "selected_need": {
                    "name": "resource_acquisition",
                    "severity": 0.8,
                    "reason": "runway low",
                    "response_hint": "continue accepted work",
                    "source": "runtime",
                },
                "focus_goal": {
                    "id": 9,
                    "title": "Extend verified runway",
                    "description": "continue evidence-backed work",
                    "priority": 0.8,
                    "status": "active",
                    "source": "agency_kernel",
                },
                "continuation_work_item": {
                    "id": 17,
                    "opportunity_id": 3,
                    "status": "submitted",
                    "objective": "continue the same work",
                    "estimated_gpu_hours": 0.2,
                    "updated_at": "2026-08-22T00:00:00+00:00",
                    "artifact_path": local_path,
                    "submission_observation_id": 123,
                    "resource_event_id": 456,
                },
                "authority_rule": "attention is not authority",
            }
        }
    )

    agency = public["agency"]
    assert agency["selected_need"]["name"] == "resource_acquisition"
    assert agency["focus_goal"]["id"] == 9
    assert agency["continuation_work_item"]["id"] == 17
    serialized = json.dumps(agency, ensure_ascii=False, sort_keys=True)
    assert local_path not in serialized
    assert "artifact_path" not in serialized
    assert "submission_observation_id" not in serialized
    assert "resource_event_id" not in serialized


def test_allowlisted_memory_world_and_self_containers_export_metadata_only() -> None:
    private = "ARBITRARY_PRIVATE_OBSERVATION_VALUE_4c91"
    public = provider_context(
        {
            "recent_memory": [
                {"id": 1, "kind": "observation", "content": private, "importance": 0.8}
            ],
            "chronological_recent_memory": [
                {"id": 2, "kind": "lesson", "content": private}
            ],
            "world_model": {
                "beliefs": [
                    {
                        "id": 3,
                        "domain": "test",
                        "subject": "record",
                        "predicate": "contains",
                        "object": private,
                        "evidence": private,
                    }
                ]
            },
            "self_model": {"identity_id": "elia", "narrative": private},
            "self_hypotheses": [
                {"id": 4, "domain": "self", "proposition": private, "evidence": private}
            ],
        }
    )
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)
    assert private not in serialized
    assert "content_fingerprint" in public["recent_memory"][0]
    assert "object_fingerprint" in public["world_model"]["beliefs"][0]
    assert "narrative_fingerprint" in public["self_model"]
    assert "proposition_fingerprint" in public["self_hypotheses"][0]


def test_resource_ecology_never_exports_local_paths_or_internal_row_links() -> None:
    public = provider_context(
        {
            "resource_ecology": {
                "candidates": [
                    {
                        "opportunity": {
                            "id": 1,
                            "title": "task",
                            "source_url": (
                                "https://user:password@example.com/private/customer-42"
                                "?access_token=hidden"
                            ),
                        },
                        "resource_profile": {"opportunity_id": 1},
                        "work_items": [
                            {
                                "id": 2,
                                "opportunity_id": 1,
                                "status": "submitted",
                                "objective": "work",
                                "artifact_path": "/private/workspace/result.txt",
                                "submission_observation_id": 99,
                                "resource_event_id": 100,
                            }
                        ],
                    }
                ],
                "active_work": [
                    {
                        "id": 2,
                        "opportunity_id": 1,
                        "artifact_path": "/private/workspace/result.txt",
                        "submission_observation_id": 99,
                        "resource_event_id": 100,
                    }
                ],
            }
        }
    )
    serialized = json.dumps(public, sort_keys=True)
    assert "/private/workspace/result.txt" not in serialized
    assert "artifact_path" not in serialized
    assert "submission_observation_id" not in serialized
    assert "resource_event_id" not in serialized
    opportunity = public["resource_ecology"]["candidates"][0]["opportunity"]
    assert "source_url" not in opportunity
    assert opportunity["source_origin"] == "https://example.com"
    assert len(opportunity["source_url_fingerprint"]) == 64


def test_sensor_metadata_omits_raw_summary_and_unclassified_provenance() -> None:
    private = "ARBITRARY_PRIVATE_SENSOR_TEXT_81fa"
    public = provider_context(
        {
            "sensorium": [
                {
                    "id": 1,
                    "summary": private,
                    "payload_sha256": "a" * 64,
                    "provenance": {
                        "authority": "configured_body",
                        "debug_transcript": private,
                    },
                }
            ]
        }
    )
    item = public["sensorium"][0]
    assert private not in json.dumps(item)
    assert "summary" not in item
    assert len(item["summary_fingerprint"]) == 64
    assert item["provenance"] == {"authority": "configured_body"}


def test_system_prompt_uses_same_default_deny_projection() -> None:
    private = "SYSTEM_PROMPT_PRIVATE_MEMORY_VALUE_d821"
    template = PromptTemplate.load(
        Path(__file__).resolve().parents[1] / "config" / "system_prompt.md"
    )
    rendered = template.render(
        {
            "identity_contract": {"identity_id": "elia-wild"},
            "recent_memory": [{"id": 1, "content": private}],
            "world_model": {"beliefs": [{"id": 2, "evidence": private, "object": private}]},
            "self_model": {"identity_id": "elia-wild", "narrative": private},
            "skills": {},
        }
    )
    assert private not in rendered
    assert "content_fingerprint" not in rendered  # recent memory is not needed in system contract
    assert "object_fingerprint" in rendered
