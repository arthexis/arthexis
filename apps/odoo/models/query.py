from __future__ import annotations

import copy
import re
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import NoReverseMatch, reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.sigils.sigil_resolver import resolve_sigil, resolve_sigils

_SIGIL_TOKEN_PATTERN = re.compile(r"\[[A-Za-z0-9_-]+[\.:=][^\[\]]+\]")


class OdooQuery(Entity):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    profile = models.ForeignKey(
        "odoo.OdooEmployee",
        on_delete=models.SET_NULL,
        related_name="queries",
        null=True,
        blank=True,
        help_text=_("Odoo employee profile used to execute this query."),
    )
    model_name = models.CharField(
        max_length=255,
        verbose_name=_("Odoo Model"),
        help_text=_("Target Odoo model name, e.g. sale.order."),
    )
    method = models.CharField(
        max_length=100,
        default="search_read",
        help_text=_("Odoo RPC method to execute."),
    )
    kwquery = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Keyword arguments for the Odoo RPC call."),
    )
    enable_public_view = models.BooleanField(default=False)
    public_view_slug = models.SlugField(unique=True, blank=True, null=True)
    public_title = models.CharField(max_length=255, blank=True)
    public_description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = _("Odoo Query")
        verbose_name_plural = _("Odoo Queries")
        db_table = "core_odooquery"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def save(self, *args, **kwargs):
        if not self.enable_public_view:
            self.public_view_slug = None

        if self.enable_public_view and not self.public_view_slug:
            if not self.pk:
                super().save(*args, **kwargs)

            base_slug = slugify(self.name or f"odoo-query-{self.pk}")
            base_slug = base_slug or f"odoo-query-{self.pk}"
            slug = base_slug
            counter = 1
            while (
                type(self)
                .objects.filter(public_view_slug=slug)
                .exclude(pk=self.pk)
                .exists()
            ):
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.public_view_slug = slug

        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if not isinstance(self.kwquery, dict):
            errors.setdefault("kwquery", []).append(
                _("Provide keyword arguments as a JSON object."),
            )
        unresolved = self._find_unresolved_sigils(self.resolve_kwquery())
        if unresolved:
            errors.setdefault("kwquery", []).append(
                _("Unresolved sigils remain: %(sigils)s")
                % {"sigils": ", ".join(sorted(unresolved))},
            )
        if errors:
            raise ValidationError(errors)

    def public_view_url(self) -> str:
        if not self.enable_public_view or not self.public_view_slug:
            return ""
        try:
            return reverse("odoo-query-public-view", args=[self.public_view_slug])
        except NoReverseMatch:  # pragma: no cover - best effort
            return ""

    def variable_defaults(self) -> dict[str, str]:
        return {
            variable.key: variable.default_value or ""
            for variable in self.variables.order_by("sort_order", "key")
        }

    def resolve_kwquery(
        self, values: dict[str, str] | None = None, resolve_value_sigils: bool = True
    ) -> dict[str, Any]:
        source = copy.deepcopy(self.kwquery or {})
        resolved_values = {k.lower(): v for k, v in (values or {}).items()}
        if not resolved_values:
            resolved_values = {
                key.lower(): value for key, value in self.variable_defaults().items()
            }
        if resolve_value_sigils:
            resolved_values = {
                key: resolve_sigils(value) for key, value in resolved_values.items()
            }
        return self._resolve_structure(source, resolved_values)

    def execute(
        self, values: dict[str, str] | None = None, resolve_value_sigils: bool = True
    ):
        if not self.profile:
            raise RuntimeError("Odoo profile not configured.")
        if not self.profile.is_verified:
            raise RuntimeError("Odoo profile is not verified.")
        resolved = self.resolve_kwquery(values, resolve_value_sigils=resolve_value_sigils)
        return self.profile.execute(self.model_name, self.method, **resolved)

    @classmethod
    def _resolve_structure(
        cls, value: Any, resolved_values: dict[str, str]
    ) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
        if isinstance(value, dict):
            return {
                key: cls._resolve_structure(child, resolved_values)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [cls._resolve_structure(child, resolved_values) for child in value]
        if isinstance(value, str):
            return cls._resolve_string(value, resolved_values)
        return value

    @classmethod
    def _resolve_string(cls, value: str, resolved_values: dict[str, str]) -> str:
        parts: list[str] = []
        i = 0
        while i < len(value):
            if value[i] == "[":
                depth = 1
                j = i + 1
                while j < len(value) and depth:
                    if value[j] == "[":
                        depth += 1
                    elif value[j] == "]":
                        depth -= 1
                    j += 1
                if depth:
                    parts.append(value[i])
                    i += 1
                    continue
                token = value[i + 1 : j - 1]
                if token.lower().startswith("var."):
                    key = token[4:].lower()
                    parts.append(resolved_values.get(key, ""))
                else:
                    parts.append(resolve_sigil(f"[{token}]"))
                i = j
            else:
                parts.append(value[i])
                i += 1
        return "".join(parts)

    @classmethod
    def _find_unresolved_sigils(cls, resolved_query: dict[str, Any]) -> set[str]:
        tokens = cls._collect_sigil_tokens(resolved_query)
        unresolved = set()
        for token in tokens:
            if resolve_sigil(token) == token:
                unresolved.add(token)
        return unresolved

    @classmethod
    def _collect_sigil_tokens(cls, value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for child in value.values():
                found.update(cls._collect_sigil_tokens(child))
        elif isinstance(value, list):
            for child in value:
                found.update(cls._collect_sigil_tokens(child))
        elif isinstance(value, str):
            for match in _SIGIL_TOKEN_PATTERN.findall(value):
                found.add(match)
        return found


class OdooQueryVariable(Entity):
    class InputType(models.TextChoices):
        TEXT = "text", _("Text")
        NUMBER = "number", _("Number")
        DATE = "date", _("Date")
        DATETIME = "datetime", _("Date & time")

    query = models.ForeignKey(
        OdooQuery, on_delete=models.CASCADE, related_name="variables"
    )
    key = models.SlugField(
        max_length=50,
        help_text=_("Identifier used in [VAR.key] sigils."),
    )
    label = models.CharField(max_length=100)
    help_text = models.CharField(max_length=255, blank=True)
    default_value = models.TextField(blank=True)
    input_type = models.CharField(
        max_length=20,
        choices=InputType.choices,
        default=InputType.TEXT,
    )
    is_required = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "label")
        verbose_name = _("Odoo Query Variable")
        verbose_name_plural = _("Odoo Query Variables")
        db_table = "core_odooqueryvariable"
        constraints = [
            models.UniqueConstraint(
                fields=["query", "key"], name="odooqueryvariable_unique"
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.query}: {self.key}"

    @property
    def sigil(self) -> str:
        return f"[VAR.{self.key}]"

    def clean(self):
        super().clean()

    def to_context(self, value: str | None) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "help_text": self.help_text,
            "value": value or "",
            "input_type": self.input_type,
            "sigil": self.sigil,
            "is_required": self.is_required,
        }


__all__ = ["OdooQuery", "OdooQueryVariable"]
