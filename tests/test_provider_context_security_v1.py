from __future__ import annotations

import json

from elia.brain import _system_and_public_context
from elia.provider_context import provider_context


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
            "world_model": {
                "source": f"https://user:pass@example.org/report?access_token={secret}&view=1",
                "nested": {
                    "password": secret,
                    "note": f"api_key={secret}",
                    "payload_sha256": digest,
                },
            },
        }
    )
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)

    assert secret not in serialized
    assert "user:pass@" not in serialized
    assert public["world_model"]["nested"]["password"] == "[REDACTED]"
    assert public["world_model"]["nested"]["payload_sha256"] == digest
    assert "[REDACTED]" in serialized
