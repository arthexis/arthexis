"""Core modeling system scaffolding for multi-dimensional interactions."""

from .adapters import AdapterError, BaseAdapter
from .events import CanonicalEvent, EventContext, EventPolicy
from .orchestration import OrchestrationError, Orchestrator, TransformerRegistry
from .registry import DimensionSpec, ModelRegistry, TransformationSpec
from .transformers import BaseTransformer

__all__ = [
    "AdapterError",
    "BaseAdapter",
    "BaseTransformer",
    "CanonicalEvent",
    "DimensionSpec",
    "EventContext",
    "EventPolicy",
    "ModelRegistry",
    "OrchestrationError",
    "Orchestrator",
    "TransformationSpec",
    "TransformerRegistry",
]
