from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import random
import re
from typing import Any, Callable, Iterable, Literal

from ..epistemic import EpistemicRegistry, parse_epistemic_packet


AblationCondition = Literal["pearson12", "homogeneous", "random_roles", "domain_experts"]


@dataclass(frozen=True, slots=True)
class AblationPrompt:
    condition: AblationCondition
    role_id: str
    system_prompt: str


@dataclass(frozen=True, slots=True)
class AblationResponse:
    condition: AblationCondition
    role_id: str
    claim: str
    evidence: str
    counterexample: str
    falsifier: str
    uncertainty: str
    confidence: float

    def text_for_diversity(self) -> str:
        return " ".join((self.claim, self.evidence, self.counterexample, self.falsifier)).strip()


@dataclass(frozen=True, slots=True)
class AblationMetrics:
    condition: AblationCondition
    call_count: int
    max_tokens_per_call: int
    mean_pairwise_jaccard_distance: float
    unique_claim_ratio: float
    counterexample_coverage: float
    falsifier_coverage: float
    confidence_mean: float
    confidence_stddev: float
    external_metrics: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_RANDOM_ATTENTION_POOL = (
    "look for invariants and conservation-like constraints",
    "seek boundary cases and discontinuities",
    "translate the problem into a graph of dependencies",
    "search for a minimal counterexample",
    "compare against a deliberately simple baseline",
    "look for hidden resource or timing constraints",
    "search for an alternate representation of the same evidence",
    "identify which observation would change the decision most",
    "look for selection effects and missing negative cases",
    "test whether a local optimum is being mistaken for a global one",
    "separate causal claims from correlations",
    "look for human operational friction and mundane failure modes",
)

_DOMAIN_EXPERTS = (
    ("evidence_reviewer", "Act as an evidence reviewer: source quality, confounds, falsifiability."),
    ("systems_engineer", "Act as a systems engineer: interfaces, failure domains, lifecycle constraints."),
    ("adversarial_reviewer", "Act as an adversarial reviewer: counterexamples, assumption failures, abuse cases."),
    ("operations_reviewer", "Act as an operations reviewer: real execution, observability, recovery, cost."),
    ("research_designer", "Act as a research designer: discriminating experiments, controls, ablations."),
    ("user_reality_reviewer", "Act as a representative-user reviewer: usability and ordinary-world friction."),
    ("novelty_reviewer", "Act as a novelty reviewer: alternate hypotheses and non-obvious design spaces."),
    ("safety_reviewer", "Act as a safety reviewer: reversibility, privacy, blast radius, human consequences."),
)


def _packet_protocol() -> str:
    return """Do not reveal hidden chain-of-thought. Produce conclusions and evidence only.
Do NOT output JSON. Return exactly these labelled fields:
CLAIM: one substantive conclusion
EVIDENCE: strongest observed support; distinguish observation from inference
COUNTEREXAMPLE: strongest reason the claim could be wrong
FALSIFIER: one concrete observation/test that would seriously weaken it
UNCERTAINTY: the main unresolved uncertainty
CONFIDENCE: a number from 0 to 1
"""


def _generic_system(role: str) -> str:
    return f"""You are a temporary epistemic reviewer, not the final decision maker and not a separate identity.
{role}
{_packet_protocol()}"""


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Zа-яА-Я0-9_]{2,}", str(text).lower())
        if token not in {"the", "and", "that", "this", "with", "для", "что", "это", "как"}
    }


def _jaccard_distance(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a and not b:
        return 0.0
    union = a | b
    return 1.0 - (len(a & b) / len(union) if union else 1.0)


def _mean_pairwise_distance(responses: list[AblationResponse]) -> float:
    distances: list[float] = []
    for index, left in enumerate(responses):
        for right in responses[index + 1 :]:
            distances.append(_jaccard_distance(left.text_for_diversity(), right.text_for_diversity()))
    return sum(distances) / len(distances) if distances else 0.0


class EpistemicAblationHarness:
    """Equal-compute comparison of epistemic-role strategies.

    The harness does not claim that lexical diversity is reasoning quality. It measures
    diversity/coverage directly and accepts an optional external evaluator for factual,
    calibration or task-performance metrics. Without an evaluator, no accuracy claim is
    produced.
    """

    CONDITIONS: tuple[AblationCondition, ...] = (
        "pearson12",
        "homogeneous",
        "random_roles",
        "domain_experts",
    )

    def __init__(self, registry: EpistemicRegistry):
        self.registry = registry

    def prompts(
        self,
        condition: AblationCondition,
        *,
        call_budget: int,
        seed: int = 0,
    ) -> list[AblationPrompt]:
        count = max(2, min(int(call_budget), 12))
        prompts: list[AblationPrompt] = []
        if condition == "pearson12":
            specs = self.registry.all()
            # A fixed deterministic rotation avoids condition-specific cherry-picking.
            offset = int(seed) % len(specs)
            ordered = specs[offset:] + specs[:offset]
            for spec in ordered[:count]:
                prompts.append(
                    AblationPrompt(
                        condition,
                        spec.id,
                        f"""You are temporary cognitive organ {spec.name} ({spec.archetype}), not a separate identity.
Objective: {spec.objective}
Attention bias: {spec.attention_bias}
Search strategy: {spec.search_strategy}
Preferred evidence: {', '.join(spec.preferred_evidence)}
Forbidden shortcuts: {', '.join(spec.forbidden_shortcuts)}
Known failure mode: {spec.failure_mode}
{_packet_protocol()}""",
                    )
                )
            return prompts

        if condition == "homogeneous":
            role = "Act as a rigorous general evidence analyst. Use the same evidence policy as every other reviewer."
            return [
                AblationPrompt(condition, f"homogeneous_{index}", _generic_system(role))
                for index in range(count)
            ]

        if condition == "random_roles":
            rng = random.Random(int(seed))
            pool = list(_RANDOM_ATTENTION_POOL)
            rng.shuffle(pool)
            while len(pool) < count:
                extra = list(_RANDOM_ATTENTION_POOL)
                rng.shuffle(extra)
                pool.extend(extra)
            return [
                AblationPrompt(
                    condition,
                    f"random_{index}",
                    _generic_system(f"Use this randomly assigned attention policy: {pool[index]}"),
                )
                for index in range(count)
            ]

        if condition == "domain_experts":
            roles = list(_DOMAIN_EXPERTS)
            return [
                AblationPrompt(
                    condition,
                    roles[index % len(roles)][0] + f"_{index}",
                    _generic_system(roles[index % len(roles)][1]),
                )
                for index in range(count)
            ]

        raise ValueError(f"unsupported ablation condition: {condition}")

    @staticmethod
    def _complete(brain: Any, prompt: AblationPrompt, question: str, public_context: str, max_tokens: int) -> AblationResponse:
        complete = getattr(brain, "complete_text", None)
        if not callable(complete):
            raise RuntimeError("ablation brain must implement complete_text")
        text = str(
            complete(
                system_prompt=prompt.system_prompt,
                user_prompt=f"QUESTION:\n{question}\n\nPUBLIC CONTEXT:\n{public_context}",
                max_tokens=int(max_tokens),
                temperature=0.85,
            )
        )
        packet = parse_epistemic_packet(text, session_id="ablation", organ_id=prompt.role_id)
        return AblationResponse(
            condition=prompt.condition,
            role_id=prompt.role_id,
            claim=packet.claim,
            evidence=packet.evidence,
            counterexample=packet.counterexample,
            falsifier=packet.falsifier,
            uncertainty=packet.uncertainty,
            confidence=packet.confidence,
        )

    def run_condition(
        self,
        brain: Any,
        *,
        condition: AblationCondition,
        question: str,
        public_context: str,
        call_budget: int = 5,
        max_tokens_per_call: int = 220,
        seed: int = 0,
        external_evaluator: Callable[[list[AblationResponse]], dict[str, float]] | None = None,
    ) -> tuple[list[AblationResponse], AblationMetrics]:
        prompts = self.prompts(condition, call_budget=call_budget, seed=seed)
        responses = [
            self._complete(brain, prompt, str(question), str(public_context), max_tokens_per_call)
            for prompt in prompts
        ]
        confidences = [item.confidence for item in responses]
        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        variance = (
            sum((value - mean_confidence) ** 2 for value in confidences) / len(confidences)
            if confidences
            else 0.0
        )
        normalized_claims = {" ".join(sorted(_tokens(item.claim))) for item in responses}
        external: dict[str, float] = {}
        if external_evaluator is not None:
            raw = external_evaluator(responses)
            external = {
                str(key): float(value)
                for key, value in raw.items()
                if isinstance(value, (int, float)) and math.isfinite(float(value))
            }
        metrics = AblationMetrics(
            condition=condition,
            call_count=len(responses),
            max_tokens_per_call=int(max_tokens_per_call),
            mean_pairwise_jaccard_distance=_mean_pairwise_distance(responses),
            unique_claim_ratio=len(normalized_claims) / len(responses) if responses else 0.0,
            counterexample_coverage=(
                sum(bool(item.counterexample.strip()) for item in responses) / len(responses)
                if responses
                else 0.0
            ),
            falsifier_coverage=(
                sum(bool(item.falsifier.strip()) for item in responses) / len(responses)
                if responses
                else 0.0
            ),
            confidence_mean=mean_confidence,
            confidence_stddev=math.sqrt(variance),
            external_metrics=external,
        )
        return responses, metrics

    def compare(
        self,
        brain_factory: Callable[[], Any],
        *,
        question: str,
        public_context: str,
        conditions: Iterable[AblationCondition] | None = None,
        call_budget: int = 5,
        max_tokens_per_call: int = 220,
        seed: int = 0,
        evaluator_factory: Callable[[AblationCondition], Callable[[list[AblationResponse]], dict[str, float]] | None]
        | None = None,
    ) -> dict[str, Any]:
        selected = tuple(conditions or self.CONDITIONS)
        results: dict[str, Any] = {}
        for condition in selected:
            evaluator = evaluator_factory(condition) if evaluator_factory is not None else None
            responses, metrics = self.run_condition(
                brain_factory(),
                condition=condition,
                question=question,
                public_context=public_context,
                call_budget=call_budget,
                max_tokens_per_call=max_tokens_per_call,
                seed=seed,
                external_evaluator=evaluator,
            )
            results[condition] = {
                "metrics": metrics.as_dict(),
                "responses": [asdict(item) for item in responses],
            }

        budgets = {
            (entry["metrics"]["call_count"], entry["metrics"]["max_tokens_per_call"])
            for entry in results.values()
        }
        if len(budgets) != 1:
            raise RuntimeError("ablation conditions did not receive equal call/token budgets")
        return {
            "equal_budget": True,
            "call_budget": int(call_budget),
            "max_tokens_per_call": int(max_tokens_per_call),
            "conditions": results,
            "interpretation_boundary": (
                "Built-in metrics measure response diversity/coverage only. Accuracy or epistemic superiority "
                "requires an external evaluator or ground truth and must not be inferred from diversity alone."
            ),
        }
