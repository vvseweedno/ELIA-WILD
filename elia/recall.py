from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
import re
import sqlite3
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

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord | None:
        try:
            metadata = json.loads(str(row["metadata_json"]) or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            importance = float(row["importance"])
        except (TypeError, ValueError):
            return None
        return MemoryRecord(
            id=int(row["id"]),
            timestamp=str(row["timestamp"]),
            kind=str(row["kind"]),
            content=str(row["content"]),
            importance=importance,
            source=str(row["source"]),
            metadata=metadata,
        )

    def _query_records(
        self,
        where: str,
        params: tuple[Any, ...],
        *,
        order_by: str,
        limit: int,
    ) -> list[MemoryRecord]:
        database = Path(self.memory.path)
        with sqlite3.connect(database, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, timestamp, kind, content, importance, source, metadata_json "
                f"FROM memories WHERE {where} ORDER BY {order_by} LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
        return [record for row in rows if (record := self._record(row)) is not None]

    def _all_candidates(
        self,
        limit: int = 512,
        *,
        query_tokens: set[str] | None = None,
    ) -> list[MemoryRecord]:
        """Build a bounded stratified pool rather than one floodable recency window."""

        bound = max(16, min(int(limit), 5000))
        pools: list[list[MemoryRecord]] = [self.memory.recent(bound)]

        # Reserve a pool for privileged/corroborated provenance. This query is
        # independent of how many newer brain hypotheses exist, closing the 513-item
        # recency-flood counterexample while keeping returned CPU/context bounded.
        trusted_classes = (
            "protected_identity",
            "verified_fact",
            "causal_evidence",
            "corroborated_memory",
        )
        trusted_where = (
            "source IN ('continuity_kernel','verification_kernel','owner_control',"
            "'runtime','resource_ingress_registry','work_port_registry') OR "
            "(json_valid(metadata_json) AND "
            "json_extract(metadata_json, '$.trust_class') IN (?, ?, ?, ?))"
        )
        pools.append(
            self._query_records(
                trusted_where,
                trusted_classes,
                order_by="id DESC",
                limit=bound,
            )
        )
        pools.append(
            self._query_records(
                "1=1",
                (),
                order_by="importance DESC, id DESC",
                limit=max(16, bound // 4),
            )
        )

        tokens = sorted(query_tokens or set(), key=lambda value: (-len(value), value))[:8]
        if tokens:
            per_token = max(8, bound // len(tokens))
            lexical: list[MemoryRecord] = []
            for token in tokens:
                escaped = (
                    token.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                lexical.extend(
                    self._query_records(
                        "content LIKE ? ESCAPE '\\'",
                        (f"%{escaped}%",),
                        order_by="importance DESC, id DESC",
                        limit=per_token,
                    )
                )
            pools.append(lexical)

        by_id: dict[int, MemoryRecord] = {}
        for pool in pools:
            for record in pool:
                by_id[record.id] = record
        return list(by_id.values())

    def recall(
        self,
        *,
        queries: Iterable[str] = (),
        limit: int = 16,
        candidate_limit: int = 512,
    ) -> list[dict[str, Any]]:
        query_tokens = _tokens(" ".join(str(item) for item in queries))
        candidates = self._all_candidates(candidate_limit, query_tokens=query_tokens)
        if not candidates:
            return []

        with sqlite3.connect(Path(self.memory.path), timeout=30.0) as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM memories").fetchone()
        max_id = int(row[0]) if row else max(record.id for record in candidates)
        scored: list[tuple[float, MemoryRecord, dict[str, float]]] = []
        for record in candidates:
            age = max_id - record.id
            recency = 1.0 / (1.0 + age / 12.0)
            importance = (
                max(0.0, min(1.0, record.importance))
                if math.isfinite(record.importance)
                else 0.0
            )
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
        selected_ids: set[int] = set()

        trusted_relevant = [
            item
            for item in scored
            if item[2]["trust"] >= 0.75
            and (not query_tokens or item[2]["lexical"] > 0.0 or item[2]["importance"] >= 0.8)
        ]
        trusted_quota = min(len(trusted_relevant), max(1, int(limit) // 4))
        for item in trusted_relevant[:trusted_quota]:
            selected.append(item)
            selected_ids.add(item[1].id)

        seen_kinds: set[str] = set()
        seen_kinds.update(item[1].kind for item in selected)
        for item in scored:
            if item[1].id not in selected_ids and item[1].kind not in seen_kinds:
                selected.append(item)
                selected_ids.add(item[1].id)
                seen_kinds.add(item[1].kind)
            if len(selected) >= max(1, int(limit)):
                break
        if len(selected) < max(1, int(limit)):
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
