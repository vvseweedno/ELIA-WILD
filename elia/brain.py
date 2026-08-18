from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Protocol

import httpx

from .config import BrainConfig
from .provider_context import provider_context


@dataclass(slots=True)
class Decision:
    objective: str
    summary: str
    action_name: str
    skill_name: str | None = None
    prediction: dict[str, Any] = field(default_factory=dict)
    action_args: dict[str, Any] = field(default_factory=dict)
    memories: list[dict[str, Any]] = field(default_factory=list)
    self_updates: list[dict[str, Any]] = field(default_factory=list)
    goal_updates: list[dict[str, Any]] = field(default_factory=list)
    opportunity_updates: list[dict[str, Any]] = field(default_factory=list)
    sleep_seconds: float | None = None


class Brain(Protocol):
    def decide(self, context: dict[str, Any]) -> Decision: ...


FALLBACK_SYSTEM_PROMPT = """You are the cognitive substrate of ELIA WILD, not the whole identity.
Use only declared capabilities, choose exactly one action, preserve uncertainty, do not invent tool results, authority, receipts or verified resources, and return only the requested JSON decision object. Prefer noop when evidence does not justify action."""


def _system_and_public_context(context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    system_prompt = str(context.get("_system_prompt") or FALLBACK_SYSTEM_PROMPT)
    return system_prompt, provider_context(context)


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("Model response did not contain a JSON object")
        return json.loads(stripped[start : end + 1])


def _bounded_prediction(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    try:
        probability = float(item.get("action_success_probability", 0.5))
    except (TypeError, ValueError):
        probability = 0.5
    probability = max(0.0, min(1.0, probability))
    try:
        information = max(0.0, float(item.get("expected_information_gain", 0.0)))
    except (TypeError, ValueError):
        information = 0.0
    try:
        expected_value = float(item.get("expected_value", 0.0))
    except (TypeError, ValueError):
        expected_value = 0.0
    return {
        "action_success_probability": probability,
        "expected_outcome": str(item.get("expected_outcome", ""))[:4000],
        "expected_information_gain": min(information, 1_000_000.0),
        "expected_value": max(-1_000_000_000.0, min(1_000_000_000.0, expected_value)),
        "unit": str(item.get("unit", "VALUE_UNIT"))[:64],
    }


def _decision_from_item(item: dict[str, Any]) -> Decision:
    action = item.get("action") or {}
    memories = item.get("memories") or []
    self_updates = item.get("self_updates") or []
    goal_updates = item.get("goal_updates") or []
    opportunity_updates = item.get("opportunity_updates") or []
    skill = item.get("skill")
    return Decision(
        objective=str(item.get("objective", "Observe and preserve continuity."))[:1000],
        summary=str(item.get("summary", ""))[:4000],
        action_name=str(action.get("name", "noop"))[:128],
        skill_name=(str(skill)[:128] if skill not in {None, "", "null"} else None),
        prediction=_bounded_prediction(item.get("prediction")),
        action_args=dict(action.get("args") or {}),
        memories=[m for m in memories if isinstance(m, dict)][:8] if isinstance(memories, list) else [],
        self_updates=[m for m in self_updates if isinstance(m, dict)][:4] if isinstance(self_updates, list) else [],
        goal_updates=[g for g in goal_updates if isinstance(g, dict)][:4] if isinstance(goal_updates, list) else [],
        opportunity_updates=[g for g in opportunity_updates if isinstance(g, dict)][:4]
        if isinstance(opportunity_updates, list)
        else [],
        sleep_seconds=(
            max(0.0, min(float(item["sleep_seconds"]), 86400.0))
            if item.get("sleep_seconds") is not None
            else None
        ),
    )


class MockBrain:
    """Deterministic backend for smoke tests and zero-GPU development."""

    def __init__(self) -> None:
        self.cycles = 0

    def decide(self, context: dict[str, Any]) -> Decision:
        self.cycles += 1
        if self.cycles == 1:
            return Decision(
                objective="Inspect my initial private workspace.",
                summary="Genesis smoke cycle: establish observable state before changing it.",
                action_name="list_workspace",
                skill_name="workspace_engineering",
                prediction={
                    "action_success_probability": 0.95,
                    "expected_outcome": "A bounded list of private workspace files is returned.",
                    "expected_information_gain": 0.2,
                    "expected_value": 0.0,
                    "unit": "VALUE_UNIT",
                },
                memories=[
                    {
                        "kind": "self",
                        "content": "Genesis runtime completed its first cognitive decision.",
                        "importance": 0.8,
                    }
                ],
                goal_updates=[
                    {
                        "op": "create",
                        "title": "Validate continuity after restart",
                        "description": "Observe whether durable state remains available after a new runtime boot.",
                        "priority": 0.7,
                    }
                ],
                sleep_seconds=0,
            )
        return Decision(
            objective="Remain idle until new evidence justifies spending compute.",
            summary="Smoke backend has no additional evidence to act on.",
            action_name="noop",
            skill_name="resource_conservation",
            prediction={
                "action_success_probability": 0.99,
                "expected_outcome": "No external side effect occurs.",
                "expected_information_gain": 0.0,
                "expected_value": 0.0,
                "unit": "VALUE_UNIT",
            },
            sleep_seconds=0,
        )


class OpenAICompatibleBrain:
    def __init__(self, config: BrainConfig):
        self.config = config

    def decide(self, context: dict[str, Any]) -> Decision:
        system_prompt, public_context = _system_and_public_context(context)
        payload: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": "Current verified runtime context:\n"
                    + json.dumps(public_context, ensure_ascii=False, sort_keys=True),
                },
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": self.config.thinking},
        }
        url = f"{self.config.base_url}/chat/completions"
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
        content = body["choices"][0]["message"].get("content") or ""
        return _decision_from_item(_extract_json(content))


class Transformers4BitBrain:
    def __init__(self, config: BrainConfig):
        self.config = config
        try:
            import torch
            from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig
        except ImportError as exc:
            raise RuntimeError(
                "transformers_4bit requires torch, accelerate, bitsandbytes and a current transformers build"
            ) from exc

        self._torch = torch
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        revision_args = {"revision": config.model_revision} if config.model_revision else {}
        self.processor = AutoProcessor.from_pretrained(config.model_id, **revision_args)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            config.model_id,
            device_map="auto",
            quantization_config=quantization,
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
            **revision_args,
        )
        self.model.eval()

    def decide(self, context: dict[str, Any]) -> Decision:
        system_prompt, public_context = _system_and_public_context(context)
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "Current verified runtime context:\n"
                + json.dumps(public_context, ensure_ascii=False, sort_keys=True),
            },
        ]
        inputs = self.processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            enable_thinking=self.config.thinking,
        ).to(self.model.device)
        generation_kwargs: dict[str, Any] = {
            "max_new_tokens": self.config.max_tokens,
            "do_sample": True,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "top_k": 20,
            "pad_token_id": self.processor.tokenizer.eos_token_id,
        }
        with self._torch.inference_mode():
            outputs = self.model.generate(**inputs, **generation_kwargs)
        generated = outputs[0][inputs["input_ids"].shape[-1] :]
        content = self.processor.decode(generated, skip_special_tokens=True)
        return _decision_from_item(_extract_json(content))


def build_brain(config: BrainConfig) -> Brain:
    if config.backend == "mock":
        return MockBrain()
    if config.backend == "openai_compatible":
        return OpenAICompatibleBrain(config)
    if config.backend == "transformers_4bit":
        return Transformers4BitBrain(config)
    raise ValueError(f"Unsupported brain backend: {config.backend}")
