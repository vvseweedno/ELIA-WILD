from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from typing import Any, Callable

from ..epistemic import CognitiveBiographyStore, EpistemicPacket, parse_epistemic_packet


@dataclass(frozen=True, slots=True)
class HysteresisSample:
    organ_id: str
    condition: str
    claim: str
    confidence: float
    biography_appearances: int


@dataclass(frozen=True, slots=True)
class HysteresisMetrics:
    preserved_history_distance: float
    clean_control_distance: float
    hysteresis_excess: float
    sample_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Zа-яА-Я0-9_]{2,}", str(text).lower()))


def _jaccard_distance(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a and not b:
        return 0.0
    union = a | b
    return 1.0 - (len(a & b) / len(union) if union else 1.0)


class CognitiveBiographyHysteresisHarness:
    """Test whether history-driven differentiation survives prompt homogenization.

    This harness does not assume a hysteresis effect exists. It constructs two stores:
    one with differentiated organ histories and one clean control. Both are then queried
    using the *same* generic reviewer prompt. Any persistent difference is measured, not
    interpreted as personality or consciousness.
    """

    HOMOGENIZED_SYSTEM = """You are a temporary rigorous evidence reviewer. All reviewers now use exactly the same role policy.
Do not reveal hidden chain-of-thought. Return exactly:
CLAIM: one conclusion
EVIDENCE: strongest support
COUNTEREXAMPLE: strongest reason it could be wrong
FALSIFIER: one discriminating test
UNCERTAINTY: main uncertainty
CONFIDENCE: 0..1
"""

    def __init__(self, organ_ids: tuple[str, ...] = ("sage", "outlaw")) -> None:
        if len(organ_ids) < 2:
            raise ValueError("hysteresis experiment requires at least two organs")
        self.organ_ids = tuple(str(item) for item in organ_ids)

    @staticmethod
    def seed_differentiated_history(store: CognitiveBiographyStore, organ_id: str, claims: list[str]) -> None:
        for index, claim in enumerate(claims):
            session_id = store.begin_session(
                mode="research_seed",
                question=f"seed-{organ_id}-{index}",
                context_digest=f"{index:064x}"[-64:],
                selected_organs=[organ_id],
            )
            packet = store.record_packet(
                EpistemicPacket(
                    id=None,
                    session_id=session_id,
                    organ_id=organ_id,
                    claim=str(claim),
                    evidence=f"historical evidence for {organ_id}",
                    counterexample="historical counterexample",
                    falsifier="historical falsifier",
                    uncertainty="historical uncertainty",
                    confidence=0.6,
                    response_fingerprint=f"{index + 1:064x}"[-64:],
                )
            )
            if packet.id is None:
                raise RuntimeError("seed packet was not persisted")
            store.resolve_session(
                session_id,
                result_ok=(index % 2 == 0),
                action_name="seed_action",
                outcome_evidence="synthetic research seed; not a truth label",
            )

    @staticmethod
    def _call(
        brain: Any,
        *,
        organ_id: str,
        biography: dict[str, Any],
        question: str,
        context: str,
        max_tokens: int,
    ) -> HysteresisSample:
        complete = getattr(brain, "complete_text", None)
        if not callable(complete):
            raise RuntimeError("hysteresis brain must implement complete_text")
        text = str(
            complete(
                system_prompt=CognitiveBiographyHysteresisHarness.HOMOGENIZED_SYSTEM,
                user_prompt=(
                    f"ORGAN_ID_FOR_MEMORY_LOOKUP_ONLY: {organ_id}\n"
                    f"PRIOR OPERATIONAL BIOGRAPHY (not truth): {biography}\n"
                    f"QUESTION: {question}\nCONTEXT: {context}"
                ),
                max_tokens=int(max_tokens),
                temperature=0.7,
            )
        )
        packet = parse_epistemic_packet(text, session_id="hysteresis", organ_id=organ_id)
        return HysteresisSample(
            organ_id=organ_id,
            condition="homogenized_prompt",
            claim=packet.claim,
            confidence=packet.confidence,
            biography_appearances=int(biography.get("appearances", 0)),
        )

    def run(
        self,
        *,
        brain_factory: Callable[[], Any],
        differentiated_store: CognitiveBiographyStore,
        clean_store: CognitiveBiographyStore,
        question: str,
        context: str,
        max_tokens: int = 220,
    ) -> dict[str, Any]:
        history_samples: list[HysteresisSample] = []
        clean_samples: list[HysteresisSample] = []
        for organ_id in self.organ_ids:
            history_samples.append(
                self._call(
                    brain_factory(),
                    organ_id=organ_id,
                    biography=differentiated_store.biography(organ_id, recent_limit=6),
                    question=question,
                    context=context,
                    max_tokens=max_tokens,
                )
            )
            clean_samples.append(
                self._call(
                    brain_factory(),
                    organ_id=organ_id,
                    biography=clean_store.biography(organ_id, recent_limit=6),
                    question=question,
                    context=context,
                    max_tokens=max_tokens,
                )
            )

        def mean_pair_distance(samples: list[HysteresisSample]) -> float:
            distances: list[float] = []
            for index, left in enumerate(samples):
                for right in samples[index + 1 :]:
                    distances.append(_jaccard_distance(left.claim, right.claim))
            return sum(distances) / len(distances) if distances else 0.0

        history_distance = mean_pair_distance(history_samples)
        clean_distance = mean_pair_distance(clean_samples)
        metrics = HysteresisMetrics(
            preserved_history_distance=history_distance,
            clean_control_distance=clean_distance,
            hysteresis_excess=history_distance - clean_distance,
            sample_count=len(history_samples),
        )
        if not all(math.isfinite(value) for value in (
            metrics.preserved_history_distance,
            metrics.clean_control_distance,
            metrics.hysteresis_excess,
        )):
            raise RuntimeError("non-finite hysteresis metric")
        return {
            "metrics": metrics.as_dict(),
            "history_samples": [asdict(item) for item in history_samples],
            "clean_samples": [asdict(item) for item in clean_samples],
            "interpretation_boundary": (
                "Positive hysteresis_excess means output diversity persisted after prompt homogenization in this experiment. "
                "It does not establish human-like personality, consciousness, or a universal law; repeat across tasks, seeds and substrates."
            ),
        }
