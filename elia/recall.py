from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any, Iterable

from .memory import MemoryRecord, MemoryStore
from .memory_trust import memory_trust_class, memory_trust_score


TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(value: str) -> set[str]:
    return {item.casefold() for item in TOKEN_RE.findall(str(value)) if len(item) > 1}


class RecallEngine:
    """Deterministic trust-aware memory recall over persistent SQLite state.

    Relevance alone is not authority. Retrieval combines trust class, recency,
    importance, lexical relevance and kind diversity. Model-authored persistent text is
    therefore useful as a hypothesis but cannot dominate verified/runtime evidence only
    by assigning itself high importance or an emotionally salient autobiographical kind.
    """

    KIND_BONUS = {
        "self": 0.12,
        "lesson": 0.10,
        "goal": 0.10,
        "uncertainty": 0.10,
        "economy": 0.06,
        "runtime_error": 0.10,
        "action_result": 0.02,
        "brain_hypothesis": 0.0,
    }

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    def _all_candidates(self, limit: int = 512) -> list[MemoryRecord]:
        # Use MemoryStore's stable parser while bounding CPU/context work.
        return self.memory.recent(max(1, min(int(limit), 5000)))

    def recall(
        self,
        *,
        queries: Iterable[str] = (),
        limit: int = 16,
        candidate_limit: int = 512,
    ) -> list[dict[str, Any]]:
        candidates = self._all_candidates(candidate_limit)
        query_tokens = _tokens(" ".join(str(item) for item in queries))
        if not candidates:
            return []

        max_id = max(record.id for record in candidates)
        scored: list[tuple[float, MemoryRecord, dict[str, float]]] = []
        for record in candidates:
            age = max_id - record.id
            recency = 1.0 / (1.0 + age / 12.0)
            importance = max(0.0, min(1.0, record.importance))
            trust = memory_trust_score(record)
            record_tokens = _tokens(record.content)
            lexical = (
                len(record_tokens & query_tokens) / len(query_tokens)
                if query_tokens
                else 0.0
            )
            kind_bonus = self.KIND_BONUS.get(record.kind, 0.0)
            score = (
                0.30 * importance
                + 0.20 * recency
                + 0.22 * lexical
                + 0.23 * trust
                + kind_bonus
            )
            scored.append(
                (
                    score,
                    record,
                    {
                        "importance": importance,
                        "recency": recency,
                        "lexical": lexical,
                        "trust": trust,
                        "kind_bonus": kind_bonus,
                    },
                )
            )

        scored.sort(key=lambda item: (-item[0], -item[1].id))

        # First pass preserves semantic diversity, but trust remains part of the score;
        # model-authored hypotheses cannot impersonate privileged `self`/`lesson` kinds
        # because MemoryTrustGate stores them as `brain_hypothesis`.
        selected: list[tuple[float, MemoryRecord, dict[str, float]]] = []
        seen_kinds: set[str] = set()
        for item in scored:
            if item[1].kind not in seen_kinds:
                selected.append(item)
                seen_kinds.add(item[1].kind)
            if len(selected) >= max(1, int(limit)):
                break
        if len(selected) < max(1, int(limit)):
            selected_ids = {item[1].id for item in selected}
            for item in scored:
                if item[1].id in selected_ids:
                    continue
                selected.append(item)
                selected_ids.add(item[1].id)
                if len(selected) >= int(limit):
                    break

        selected.sort(key=lambda item: item[1].id)
        return [
            {
                **asdict(record),
                "trust_class": memory_trust_class(record),
                "recall_score": score,
                "recall_components": components,
            }
            for score, record, components in selected
        ]
