from __future__ import annotations

from dataclasses import dataclass

from .events import CanonicalEvent
from .registry import ModelRegistry, TransformationSpec
from .transformers import BaseTransformer


class OrchestrationError(RuntimeError):
    pass


@dataclass
class TransformerRegistry:
    _transformers: dict[tuple[str, str, str], BaseTransformer]

    def __init__(self) -> None:
        self._transformers = {}

    def register(self, transformer: BaseTransformer) -> None:
        spec = transformer.spec
        key = (spec.source_dimension_id, spec.target_dimension_id, spec.name)
        if key in self._transformers:
            raise OrchestrationError(
                "transformer already registered for "
                f"{spec.source_dimension_id}->{spec.target_dimension_id}:{spec.name}"
            )
        self._transformers[key] = transformer

    def get(self, spec: TransformationSpec) -> BaseTransformer:
        key = (spec.source_dimension_id, spec.target_dimension_id, spec.name)
        try:
            return self._transformers[key]
        except KeyError as exc:
            raise OrchestrationError(
                "missing transformer for "
                f"{spec.source_dimension_id}->{spec.target_dimension_id}:{spec.name}"
            ) from exc


@dataclass
class Orchestrator:
    registry: ModelRegistry
    transformers: TransformerRegistry

    def route_event(self, event: CanonicalEvent, target_dimension_id: str) -> CanonicalEvent:
        if event.dimension_id == target_dimension_id:
            return event
        path = self.registry.find_path(event.dimension_id, target_dimension_id)
        current_event = event
        for spec in path:
            transformer = self.transformers.get(spec)
            current_event = transformer.transform(current_event)
        return current_event
