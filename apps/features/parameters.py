"""Runtime parameter accessors and schema definitions for suite features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.cache import cache
from django.db.utils import OperationalError, ProgrammingError
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True, slots=True)
class FeatureParameterDefinition:
    """Describe a mutable feature parameter exposed in the admin UI."""

    key: str
    label: str
    help_text: str
    choices: tuple[tuple[str, str], ...] = ()
    default: str = ""

    def normalize(self, raw_value: object) -> str:
        """Return a normalized parameter value or raise ``ValueError``."""

        value = "" if raw_value is None else str(raw_value).strip()
        if not value:
            return self.default

        if self.choices:
            allowed = {choice for choice, _ in self.choices}
            if value not in allowed:
                raise ValueError(
                    f"Unsupported value '{value}' for parameter '{self.key}'."
                )
        return value


# Intentional subset for the operator experience; keep in sync manually with product requirements.
OPERATOR_LANGUAGE_CHOICES: tuple[tuple[str, str], ...] = (
    ("en", _("English")),
    ("de", _("German")),
    ("es", _("Spanish")),
    ("it", _("Italian")),
)


FEATURE_PARAMETER_DEFINITIONS: dict[str, tuple[FeatureParameterDefinition, ...]] = {
    "ocpp-simulator": (
        FeatureParameterDefinition(
            key="arthexis_backend",
            label=_("Arthexis backend"),
            help_text=_("Enable or disable the legacy Arthexis simulator backend."),
            choices=(("enabled", _("Enabled")), ("disabled", _("Disabled"))),
            default="enabled",
        ),
        FeatureParameterDefinition(
            key="mobilityhouse_backend",
            label=_("Mobility House backend"),
            help_text=_("Enable or disable the Mobility House simulator backend."),
            choices=(("enabled", _("Enabled")), ("disabled", _("Disabled"))),
            default="disabled",
        ),
    ),
    "odoo-crm-sync": (
        FeatureParameterDefinition(
            key="deployment_discovery",
            label=_("Deployment discovery"),
            help_text=_(
                "Enable or disable discovery sync of local Odoo deployments."
            ),
            choices=(("enabled", _("Enabled")), ("disabled", _("Disabled"))),
            default="enabled",
        ),
        FeatureParameterDefinition(
            key="employee_import",
            label=_("Employee import"),
            help_text=_(
                "Enable or disable importing missing local Odoo employee profiles."
            ),
            choices=(("enabled", _("Enabled")), ("disabled", _("Disabled"))),
            default="enabled",
        ),
        FeatureParameterDefinition(
            key="evergo_users",
            label=_("Evergo users"),
            help_text=_(
                "Enable or disable provisioning Odoo users from Evergo users."
            ),
            choices=(("enabled", _("Enabled")), ("disabled", _("Disabled"))),
            default="enabled",
        ),
    ),
    "operator-site-interface": (
        FeatureParameterDefinition(
            key="default_language",
            label=_("Default language"),
            help_text=_(
                "Fallback UI language used when users have not selected a language yet."
            ),
            choices=OPERATOR_LANGUAGE_CHOICES,
            default="en",
        ),
    ),
}


def get_feature_parameter_definitions(slug: str) -> tuple[FeatureParameterDefinition, ...]:
    """Return parameter definitions for ``slug``."""

    return FEATURE_PARAMETER_DEFINITIONS.get((slug or "").strip(), ())


def get_feature_parameter_value(feature, key: str, *, default: str = "") -> str:
    """Read one feature parameter value from metadata safely."""

    metadata = getattr(feature, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return default
    params = metadata.get("parameters", {})
    if not isinstance(params, dict):
        return default
    value = params.get(key)
    if value is None:
        return default
    return str(value).strip()


def get_feature_parameter(slug: str, key: str, *, fallback: str = "") -> str:
    """Return a normalized runtime parameter value for feature ``slug``."""

    from .models import Feature

    cache_key = f"feature-param:{slug}:{key}"
    cached_value = cache.get(cache_key)
    if isinstance(cached_value, str):
        return cached_value

    definition_map = {d.key: d for d in get_feature_parameter_definitions(slug)}
    definition = definition_map.get(key)
    if definition is None:
        return fallback

    try:
        feature = Feature.objects.filter(slug=slug).only("metadata").first()
    except (OperationalError, ProgrammingError):
        return fallback if fallback is not None else definition.default

    raw_value = get_feature_parameter_value(feature, key, default=definition.default) if feature else definition.default
    try:
        normalized_value = definition.normalize(raw_value)
    except ValueError:
        normalized_value = fallback if fallback is not None else definition.default

    cache.set(cache_key, normalized_value, timeout=300)
    return normalized_value


def set_feature_parameter_values(feature, values: dict[str, Any]) -> None:
    """Persist normalized parameter values into ``feature.metadata``.

    ``set_feature_parameter_values`` mutates ``feature.metadata`` only and does not
    call ``feature.save()``; callers such as admin ``save_model`` are responsible
    for persistence. Each ``definition.normalize`` call may raise ``ValueError``,
    so validate data before calling this helper when partial updates are undesired.
    """

    metadata = getattr(feature, "metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    params = metadata.get("parameters", {})
    if not isinstance(params, dict):
        params = {}

    definitions = {d.key: d for d in get_feature_parameter_definitions(feature.slug)}
    for key, raw_value in values.items():
        definition = definitions.get(key)
        if definition is None:
            continue
        params[key] = definition.normalize(raw_value)
        cache.delete(f"feature-param:{feature.slug}:{key}")

    metadata["parameters"] = params
    feature.metadata = metadata
