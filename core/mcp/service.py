"""Core domain services shared by the MCP sigil resolver."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Mapping, MutableMapping, Sequence

from django.apps import apps
from django.db import models

from core.models import SigilRoot
from core.sigil_context import clear_context, set_context
from core.sigil_resolver import resolve_sigils

from .schemas import (
    ContextAssignments,
    MutableContextAssignments,
    ResolveOptions,
    SigilRootDescription,
)

_SIGIL_PATTERN = re.compile(r"\[([^\[\]]+)\]")


@dataclass
class ResolutionResult:
    """Represents the outcome of a text resolution operation."""

    resolved: str
    unresolved: list[str]


@dataclass
class SigilSessionState:
    """Per-connection state maintained by the MCP resolver."""

    context: MutableMapping[type[models.Model], str] = field(default_factory=dict)

    def copy_context(self) -> dict[type[models.Model], str]:
        return dict(self.context)


class SigilRootCatalog:
    """Caches ``SigilRoot`` metadata for fast lookups."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, SigilRootDescription] = {}
        self.refresh()

    def refresh(self) -> None:
        """Refresh the in-memory cache from the database."""

        with self._lock:
            self._entries.clear()
            for root in SigilRoot.objects.select_related("content_type"):
                self._entries[root.prefix.upper()] = self._describe_instance(root)

    def update_from_instance(self, root: SigilRoot) -> None:
        """Update a single catalog entry using the provided model instance."""

        with self._lock:
            self._entries[root.prefix.upper()] = self._describe_instance(root)

    def remove(self, prefix: str) -> None:
        """Remove a cached entry when a ``SigilRoot`` is deleted."""

        with self._lock:
            self._entries.pop(prefix.upper(), None)

    def describe(self, prefix: str) -> SigilRootDescription:
        """Return metadata for the requested prefix."""

        with self._lock:
            entry = self._entries.get(prefix.upper())
            if entry is None:
                raise ValueError(f"Unknown sigil root: {prefix}")
            return entry.model_copy(deep=True)

    def list_roots(self) -> list[SigilRootDescription]:
        """Return all catalogued roots sorted by prefix."""

        with self._lock:
            return [
                entry.model_copy(deep=True)
                for _, entry in sorted(self._entries.items())
            ]

    def _describe_instance(self, root: SigilRoot) -> SigilRootDescription:
        model_label: str | None = None
        fields: list[str] = []
        if root.content_type:
            model = root.content_type.model_class()
            if model is not None:
                model_label = f"{model._meta.app_label}.{model._meta.object_name}"
                fields = sorted(field.name.upper() for field in model._meta.fields)
        return SigilRootDescription(
            prefix=root.prefix.upper(),
            contextType=root.context_type,
            model=model_label,
            fields=fields,
        )


class SigilResolverService:
    """High-level operations exposed by the MCP sigil resolver."""

    def __init__(self, catalog: SigilRootCatalog | None = None) -> None:
        self.catalog = catalog or SigilRootCatalog()

    def resolve_text(
        self,
        text: str,
        *,
        session_context: Mapping[type[models.Model], str] | None = None,
        overrides: ContextAssignments | None = None,
        options: ResolveOptions | None = None,
    ) -> ResolutionResult:
        """Resolve sigils in ``text`` applying any provided context."""

        merged_context = self._merge_context(session_context, overrides)
        if merged_context:
            set_context(merged_context)
        try:
            resolved = resolve_sigils(text)
        finally:
            if merged_context:
                clear_context()
        unresolved = self._collect_unresolved(text, resolved)
        if options and options.skip_unknown and unresolved:
            resolved = self._strip_unresolved(resolved, unresolved)
        return ResolutionResult(resolved=resolved, unresolved=unresolved)

    def resolve_single(
        self,
        sigil: str,
        *,
        session_context: Mapping[type[models.Model], str] | None = None,
        overrides: ContextAssignments | None = None,
    ) -> str:
        """Resolve a single sigil token returning its value."""

        token = sigil.strip()
        if not token:
            raise ValueError("Sigil token cannot be empty")
        if not token.startswith("["):
            token = f"[{token}]"
        if not token.endswith("]"):
            raise ValueError("Sigil tokens must end with ']'")
        result = self.resolve_text(
            token, session_context=session_context, overrides=overrides
        )
        return result.resolved

    def describe_root(self, prefix: str) -> SigilRootDescription:
        """Return cached metadata for the requested sigil root."""

        return self.catalog.describe(prefix)

    def list_roots(self) -> list[SigilRootDescription]:
        """Return all known sigil roots."""

        return self.catalog.list_roots()

    def update_session_context(
        self,
        state: SigilSessionState,
        assignments: MutableContextAssignments | None,
    ) -> list[str]:
        """Persist ``assignments`` into the session's context map."""

        if assignments is None:
            state.context.clear()
            return []

        if not assignments:
            state.context.clear()
            return []

        normalized = self._normalize_context(assignments)
        removed: list[type[models.Model]] = []
        for label, raw_value in assignments.items():
            model = self._resolve_model_label(label)
            if raw_value is None or raw_value == "":
                removed.append(model)
        for model in removed:
            state.context.pop(model, None)
        state.context.update(normalized)
        return sorted(self._format_model_label(model) for model in state.context.keys())

    def _merge_context(
        self,
        session_context: Mapping[type[models.Model], str] | None,
        overrides: ContextAssignments | None,
    ) -> dict[type[models.Model], str]:
        merged: dict[type[models.Model], str] = {}
        if session_context:
            merged.update(session_context)
        if overrides:
            merged.update(self._normalize_context(overrides))
        return merged

    def _normalize_context(
        self, context: ContextAssignments | MutableContextAssignments
    ) -> dict[type[models.Model], str]:
        normalized: dict[type[models.Model], str] = {}
        for label, value in context.items():
            if value is None or value == "":
                continue
            model = self._resolve_model_label(label)
            normalized[model] = str(value)
        return normalized

    def _resolve_model_label(self, label: str) -> type[models.Model]:
        if not label or "." not in label:
            raise ValueError("Context keys must be formatted as 'app_label.ModelName'")
        app_label, model_name = label.split(".", 1)
        model = apps.get_model(app_label, model_name)
        if model is None:
            raise ValueError(f"Unknown model for context assignment: {label}")
        if not issubclass(model, models.Model):  # pragma: no cover - defensive
            raise ValueError(
                f"Context assignments must reference Django models: {label}"
            )
        return model

    def _format_model_label(self, model: type[models.Model]) -> str:
        return f"{model._meta.app_label}.{model._meta.object_name}"

    def _collect_unresolved(self, original: str, resolved: str) -> list[str]:
        source_tokens = {match.group(1) for match in _SIGIL_PATTERN.finditer(original)}
        unresolved: list[str] = []
        for match in _SIGIL_PATTERN.finditer(resolved):
            token = match.group(1)
            if token in source_tokens and token not in unresolved:
                unresolved.append(token)
        return unresolved

    def _strip_unresolved(self, text: str, unresolved: Sequence[str]) -> str:
        if not unresolved:
            return text
        unresolved_set = set(unresolved)
        return _SIGIL_PATTERN.sub(
            lambda match: "" if match.group(1) in unresolved_set else match.group(0),
            text,
        )
