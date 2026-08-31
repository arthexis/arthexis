from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class DimensionSpec:
    dimension_id: str
    name: str
    description: str = ""
    schema: dict[str, Any] | None = None
    capabilities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    policy: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransformationSpec:
    source_dimension_id: str
    target_dimension_id: str
    name: str
    version: str
    lossy_fields: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()


class ModelRegistry:
    def __init__(self) -> None:
        self._dimensions: dict[str, DimensionSpec] = {}
        self._transformations: dict[tuple[str, str, str], TransformationSpec] = {}

    def register_dimension(self, spec: DimensionSpec) -> None:
        if not spec.dimension_id.strip():
            raise ValueError("dimension_id is required")
        if spec.dimension_id in self._dimensions:
            raise ValueError(f"dimension '{spec.dimension_id}' already registered")
        self._dimensions[spec.dimension_id] = spec

    def register_transformation(self, spec: TransformationSpec) -> None:
        key = (spec.source_dimension_id, spec.target_dimension_id, spec.name)
        if key in self._transformations:
            raise ValueError(
                "transformation already registered for "
                f"{spec.source_dimension_id}->{spec.target_dimension_id}:{spec.name}"
            )
        if spec.source_dimension_id not in self._dimensions:
            raise ValueError(f"unknown source dimension '{spec.source_dimension_id}'")
        if spec.target_dimension_id not in self._dimensions:
            raise ValueError(f"unknown target dimension '{spec.target_dimension_id}'")
        self._transformations[key] = spec

    def get_dimension(self, dimension_id: str) -> DimensionSpec:
        return self._dimensions[dimension_id]

    def list_dimensions(self) -> Iterable[DimensionSpec]:
        return self._dimensions.values()

    def list_transformations(
        self,
        *,
        source_dimension_id: str | None = None,
        target_dimension_id: str | None = None,
    ) -> Iterable[TransformationSpec]:
        for spec in self._transformations.values():
            if source_dimension_id and spec.source_dimension_id != source_dimension_id:
                continue
            if target_dimension_id and spec.target_dimension_id != target_dimension_id:
                continue
            yield spec

    def find_path(self, source_dimension_id: str, target_dimension_id: str) -> list[TransformationSpec]:
        if source_dimension_id == target_dimension_id:
            return []
        adjacency: dict[str, list[TransformationSpec]] = {}
        for spec in self._transformations.values():
            adjacency.setdefault(spec.source_dimension_id, []).append(spec)
        queue: list[tuple[str, list[TransformationSpec]]] = [(source_dimension_id, [])]
        visited = {source_dimension_id}
        while queue:
            current, path = queue.pop(0)
            for spec in adjacency.get(current, []):
                if spec.target_dimension_id in visited:
                    continue
                new_path = path + [spec]
                if spec.target_dimension_id == target_dimension_id:
                    return new_path
                visited.add(spec.target_dimension_id)
                queue.append((spec.target_dimension_id, new_path))
        raise ValueError(
            f"no transformation path from '{source_dimension_id}' to '{target_dimension_id}'"
        )
