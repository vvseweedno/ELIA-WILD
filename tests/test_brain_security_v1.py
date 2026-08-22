from __future__ import annotations

import json

import pytest

from elia import brain as brain_module
from elia.brain import (
    OpenAICompatibleBrain,
    _safe_decision_from_text,
    _transformers_generation_kwargs,
    _validated_openai_base_url,
)
from elia.config import BrainConfig


def _decision(**updates) -> dict:
    item = {
        "objective": "Take one bounded observation.",
        "summary": "The declared read is reversible.",
        "skill": None,
        "prediction": {
            "action_success_probability": 0.8,
            "expected_outcome": "A bounded result is observed.",
            "expected_information_gain": 0.2,
            "expected_value": 0.0,
            "unit": "VALUE_UNIT",
        },
        "action": {"name": "noop", "args": {}},
        "memories": [],
        "self_updates": [],
        "goal_updates": [],
        "opportunity_updates": [],
        "sleep_seconds": 0,
    }
    item.update(updates)
    return item


def _config(base_url: str = "http://127.0.0.1:8000/v1") -> BrainConfig:
    return BrainConfig(
        backend="openai_compatible",
        model_id="test-model",
        base_url=base_url,
        timeout_seconds=5,
        max_tokens=128,
        temperature=0.2,
        top_p=0.9,
        thinking=False,
    )


@pytest.mark.parametrize(
    "raw",
    [
        lambda: json.dumps({**_decision(), "unexpected": True}),
        lambda: "prefix " + json.dumps(_decision()),
        lambda: json.dumps(
            _decision(
                goal_updates=[
                    {
                        "op": "create",
                        "title": "bad typed priority",
                        "priority": "high",
                    }
                ]
            )
        ),
        lambda: json.dumps(_decision()).replace('"args": {}', '"args": {"x": NaN}'),
    ],
)
def test_invalid_untrusted_decision_fails_to_noop(raw) -> None:
    decision = _safe_decision_from_text(raw())
    assert decision.action_name == "noop"
    assert decision.summary.startswith("Untrusted model decision rejected")


def test_duplicate_json_fields_are_rejected_instead_of_last_value_winning() -> None:
    raw = (
        '{"objective":"x","summary":"x",'
        '"prediction":{"action_success_probability":1,"expected_outcome":"x",'
        '"expected_information_gain":0,"expected_value":0,"unit":"VALUE_UNIT"},'
        '"action":{"name":"http_get","name":"noop","args":{}}}'
    )
    decision = _safe_decision_from_text(raw)
    assert decision.action_name == "noop"
    assert "rejected" in decision.summary.lower()


def test_strict_typed_decision_accepts_finite_contract() -> None:
    decision = _safe_decision_from_text(json.dumps(_decision()))
    assert decision.action_name == "noop"
    assert decision.objective == "Take one bounded observation."
    assert decision.prediction["action_success_probability"] == 0.8


def test_non_loopback_plaintext_model_endpoint_is_rejected() -> None:
    with pytest.raises(ValueError, match="require HTTPS"):
        _validated_openai_base_url("http://models.example.org/v1")
    assert _validated_openai_base_url("http://127.0.0.1:8000/v1").startswith("http://")
    assert _validated_openai_base_url("https://models.example.org/v1").startswith("https://")


def test_transformers_zero_temperature_is_greedy_not_hidden_sampling() -> None:
    greedy = _transformers_generation_kwargs(
        requested_tokens=64,
        configured_tokens=128,
        temperature=0.0,
        top_p=0.9,
        timeout_seconds=5.0,
        pad_token_id=17,
    )
    assert greedy == {
        "max_new_tokens": 64,
        "max_time": 5.0,
        "do_sample": False,
        "pad_token_id": 17,
    }

    sampled = _transformers_generation_kwargs(
        requested_tokens=256,
        configured_tokens=128,
        temperature=0.2,
        top_p=0.9,
        timeout_seconds=900.0,
        pad_token_id=17,
    )
    assert sampled == {
        "max_new_tokens": 128,
        "max_time": 600.0,
        "do_sample": True,
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 20,
        "pad_token_id": 17,
    }


def test_complete_text_scrubs_system_and_user_at_final_http_boundary(monkeypatch) -> None:
    captured: dict = {}

    class Response:
        headers: dict[str, str] = {}
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield json.dumps(
                {"choices": [{"message": {"content": "bounded response"}}]}
            ).encode("utf-8")

    class Client:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, *, json):
            captured.update({"method": method, "url": url, "payload": json})
            return Response()

    monkeypatch.setattr(brain_module.httpx, "Client", Client)
    backend = OpenAICompatibleBrain(_config())
    token = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    result = backend.complete_text(
        system_prompt=f"Authorization: Bearer {token}",
        user_prompt="contact owner.private@example.org or +1 202 555 0123",
        max_tokens=64,
        temperature=0.2,
    )

    assert result == "bounded response"
    outbound = json.dumps(captured["payload"], sort_keys=True)
    assert token not in outbound
    assert "owner.private@example.org" not in outbound
    assert "+1 202 555 0123" not in outbound
    assert outbound.count("[REDACTED]") >= 3
