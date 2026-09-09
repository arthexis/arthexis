from __future__ import annotations

from dataclasses import dataclass

import pytest

from apps.core.modeling import (
    CanonicalEvent,
    DimensionSpec,
    ModelRegistry,
    Orchestrator,
    TransformationSpec,
    TransformerRegistry,
)
from apps.core.modeling.transformers import BaseTransformer


@dataclass(frozen=True)
class PrefixTransformer(BaseTransformer):
    prefix: str

    def transform(self, event: CanonicalEvent) -> CanonicalEvent:
        payload = dict(event.payload)
        payload["note"] = f"{self.prefix}:{payload.get('note', 'original')}"
        return CanonicalEvent.new(
            dimension_id=self.spec.target_dimension_id,
            intent=event.intent,
            payload=payload,
            origin_surface=event.context.origin_surface,
            trace_id=event.context.trace_id,
            actor=event.context.actor,
            policy=event.policy,
            metadata=dict(event.context.metadata),
        )


def _registry_with_dimensions() -> ModelRegistry:
    registry = ModelRegistry()
    registry.register_dimension(
        DimensionSpec(
            dimension_id="cli",
            name="CLI",
            capabilities=("command",),
        )
    )
    registry.register_dimension(
        DimensionSpec(
            dimension_id="ocpp.control",
            name="OCPP Control",
            capabilities=("start_transaction",),
        )
    )
    registry.register_dimension(
        DimensionSpec(
            dimension_id="billing",
            name="Billing",
            capabilities=("charge",),
        )
    )
    return registry


def test_registry_requires_unique_dimensions() -> None:
    registry = _registry_with_dimensions()
    with pytest.raises(ValueError, match="already registered"):
        registry.register_dimension(DimensionSpec(dimension_id="cli", name="CLI"))


def test_registry_path_resolution() -> None:
    registry = _registry_with_dimensions()
    registry.register_transformation(
        TransformationSpec(
            source_dimension_id="cli",
            target_dimension_id="ocpp.control",
            name="cli_to_ocpp",
            version="1.0",
        )
    )
    registry.register_transformation(
        TransformationSpec(
            source_dimension_id="ocpp.control",
            target_dimension_id="billing",
            name="ocpp_to_billing",
            version="1.0",
        )
    )
    path = registry.find_path("cli", "billing")
    assert [spec.name for spec in path] == ["cli_to_ocpp", "ocpp_to_billing"]


def test_orchestrator_routes_across_transformations() -> None:
    registry = _registry_with_dimensions()
    registry.register_transformation(
        TransformationSpec(
            source_dimension_id="cli",
            target_dimension_id="ocpp.control",
            name="cli_to_ocpp",
            version="1.0",
        )
    )
    registry.register_transformation(
        TransformationSpec(
            source_dimension_id="ocpp.control",
            target_dimension_id="billing",
            name="ocpp_to_billing",
            version="1.0",
        )
    )
    transformers = TransformerRegistry()
    transformers.register(
        PrefixTransformer(
            spec=TransformationSpec(
                source_dimension_id="cli",
                target_dimension_id="ocpp.control",
                name="cli_to_ocpp",
                version="1.0",
            ),
            prefix="stage1",
        )
    )
    transformers.register(
        PrefixTransformer(
            spec=TransformationSpec(
                source_dimension_id="ocpp.control",
                target_dimension_id="billing",
                name="ocpp_to_billing",
                version="1.0",
            ),
            prefix="stage2",
        )
    )
    orchestrator = Orchestrator(registry=registry, transformers=transformers)
    event = CanonicalEvent.new(
        dimension_id="cli",
        intent="start_transaction",
        payload={"note": "initial"},
        origin_surface="cli",
    )
    result = orchestrator.route_event(event, "billing")
    assert result.dimension_id == "billing"
    assert result.payload["note"] == "stage2:stage1:initial"
