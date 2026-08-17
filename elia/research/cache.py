from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .memory import MemoryBackend, build_memory_backend


@dataclass(slots=True)
class StatefulMemoryCache:
    """Per-stream recurrent-memory cache for Holo/LRU/Fractal research backends.

    It is deliberately outside Genesis core persistence. The abstraction lets model
    adapters carry recurrent state across token/chunk steps without making a specific
    memory backend synonymous with ELIA identity.
    """

    backend_name: str = "lru_scan"
    backend_kwargs: dict[str, Any] = field(default_factory=dict)
    max_streams: int = 128
    _streams: dict[str, MemoryBackend] = field(default_factory=dict)

    def _key(self, stream_id: str) -> str:
        key = str(stream_id).strip()
        if not key:
            raise ValueError("stream_id is required")
        return key[:256]

    def get(self, stream_id: str) -> MemoryBackend:
        key = self._key(stream_id)
        existing = self._streams.get(key)
        if existing is not None:
            return existing
        if len(self._streams) >= max(1, int(self.max_streams)):
            # Deterministic FIFO-like eviction using insertion order.
            oldest = next(iter(self._streams))
            del self._streams[oldest]
        backend = build_memory_backend(self.backend_name, **dict(self.backend_kwargs))
        self._streams[key] = backend
        return backend

    def step(
        self,
        stream_id: str,
        value: float | complex,
        *,
        gate: float | None = None,
    ) -> float | complex:
        return self.get(stream_id).step(value, gate=gate)

    def reset(self, stream_id: str | None = None) -> None:
        if stream_id is None:
            self._streams.clear()
            return
        self._streams.pop(self._key(stream_id), None)

    def snapshot(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "stream_count": len(self._streams),
            "streams": {key: backend.state() for key, backend in self._streams.items()},
        }
