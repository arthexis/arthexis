from __future__ import annotations

from dataclasses import dataclass

from .events import CanonicalEvent
from .registry import TransformationSpec


@dataclass(frozen=True)
class BaseTransformer:
    spec: TransformationSpec

    def transform(self, event: CanonicalEvent) -> CanonicalEvent:
        raise NotImplementedError("transform must be implemented by subclasses")
