"""Compatibility exports for fixture helpers now owned by apps.base."""

from apps.base.fixtures import _model_has_seed_field, ensure_seed_data_flags

__all__ = ["ensure_seed_data_flags"]
