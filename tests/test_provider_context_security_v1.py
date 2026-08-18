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


def test_provider_context_drops_all_private_runtime_keys() -> None:
    public = provider_context({"visible": {"x": 1}, "_private": "never-forward"})
    assert public == {"visible": {"x": 1}}
