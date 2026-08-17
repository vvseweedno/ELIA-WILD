from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Iterable, Mapping, Sequence


TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokens(text: str) -> list[str]:
    return [item.casefold() for item in TOKEN_RE.findall(str(text))]


def type_token_ratio(text: str) -> float:
    items = tokens(text)
    return len(set(items)) / len(items) if items else 0.0


def token_jaccard(left: str, right: str) -> float:
    a, b = set(tokens(left)), set(tokens(right))
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def needle_score(haystack: str, needle: str) -> float:
    """Exact-token needle preservation score, independent of location."""
    target = tokens(needle)
    if not target:
        return 1.0
    source = tokens(haystack)
    if len(source) < len(target):
        return 0.0
    for index in range(len(source) - len(target) + 1):
        if source[index : index + len(target)] == target:
            return 1.0
    overlap = Counter(source) & Counter(target)
    return sum(overlap.values()) / len(target)


def associative_transitivity_score(
    relations: Mapping[str, str],
    predictions: Mapping[tuple[str, str], str],
) -> float:
    """Evaluate A→B, B→C ⇒ expected A→C association answers."""
    cases: list[bool] = []
    for a, b in relations.items():
        c = relations.get(b)
        if c is None:
            continue
        predicted = predictions.get((a, c))
        if predicted is None:
            cases.append(False)
        else:
            cases.append(str(predicted) == str(c))
    return sum(cases) / len(cases) if cases else 1.0


def multiset_pattern_retention(reference: str, scrambled: str) -> float:
    """Measure token-content preservation under ordering/scrambling stress."""
    left = Counter(tokens(reference))
    right = Counter(tokens(scrambled))
    denominator = sum(left.values())
    return sum((left & right).values()) / denominator if denominator else 1.0


def generation_stability(samples: Sequence[str]) -> dict[str, float]:
    """Lightweight diagnostics; lexical similarity is never an identity criterion."""
    if not samples:
        return {"mean_pairwise_jaccard": 1.0, "mean_ttr": 0.0}
    pairwise: list[float] = []
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            pairwise.append(token_jaccard(samples[i], samples[j]))
    return {
        "mean_pairwise_jaccard": sum(pairwise) / len(pairwise) if pairwise else 1.0,
        "mean_ttr": sum(type_token_ratio(item) for item in samples) / len(samples),
    }


@dataclass(frozen=True, slots=True)
class StressResult:
    name: str
    score: float
    passed: bool
    note: str


def run_basic_stress_suite(
    *,
    long_context: str,
    needle: str,
    generations: Sequence[str],
    reference_pattern: str,
    scrambled_pattern: str,
    threshold: float = 0.8,
) -> list[StressResult]:
    needle_value = needle_score(long_context, needle)
    stability = generation_stability(generations)
    pattern = multiset_pattern_retention(reference_pattern, scrambled_pattern)
    return [
        StressResult("needle", needle_value, needle_value >= threshold, "needle-in-context retention"),
        StressResult(
            "generation_stability",
            stability["mean_pairwise_jaccard"],
            True,
            "diagnostic only; lexical stability does not define identity",
        ),
        StressResult(
            "pattern_retention",
            pattern,
            pattern >= threshold,
            "content preservation under scrambling",
        ),
    ]
