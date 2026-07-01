"""Tiny TTL cache for GUI API responses."""

from __future__ import annotations

import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl = ttl_seconds
        self._at = 0.0
        self._value: T | None = None

    def get(self, loader: Callable[[], T]) -> T:
        now = time.monotonic()
        if self._value is not None and (now - self._at) < self.ttl:
            return self._value
        self._value = loader()
        self._at = now
        return self._value

    def clear(self) -> None:
        self._value = None
        self._at = 0.0
