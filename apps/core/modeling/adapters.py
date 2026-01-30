from __future__ import annotations

from dataclasses import dataclass

from .events import CanonicalEvent


class AdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class BaseAdapter:
    surface_name: str
    supported_dimensions: tuple[str, ...]

    def emit_event(self, raw_input: object) -> CanonicalEvent:
        raise NotImplementedError("emit_event must be implemented by subclasses")

    def render_event(self, event: CanonicalEvent) -> object:
        raise NotImplementedError("render_event must be implemented by subclasses")

    def validate_dimension(self, dimension_id: str) -> None:
        if dimension_id not in self.supported_dimensions:
            raise AdapterError(
                f"adapter '{self.surface_name}' does not support dimension '{dimension_id}'"
            )
