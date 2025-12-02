import inspect
import logging
from typing import Callable

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.module_loading import import_string
from django.utils.translation import gettext, gettext_lazy as _

from apps.base.models import Entity
from apps.locals.caches import CacheStoreMixin
from apps.sigils.fields import ConditionTextField

from .badge_utils import BadgeCounterResult
from .dashboard_rules import (
    DEFAULT_SUCCESS_MESSAGE,
    bind_rule_model,
    load_callable,
    rule_failure,
    rule_success,
)

logger = logging.getLogger(__name__)

_BADGE_COUNTER_CACHE_PREFIX = "admin.dashboard.badge_counters"
_DASHBOARD_RULE_CACHE_PREFIX = "admin.dashboard.rules"


class StoredCounter(CacheStoreMixin, Entity):
    """Base model for cached dashboard counters.

    Instances cache their computed values indefinitely and rely on explicit
    invalidation through :meth:`invalidate_model_cache` when source data
    changes.
    """

    cache_prefix: str = ""
    cache_timeout: int | float | None = None
    cache_refresh_interval = None

    class Meta:
        abstract = True

    @classmethod
    def cache_key_for_content_type(cls, content_type_id: int) -> str:
        return cls.cache_key_for_identifier(content_type_id)

    @classmethod
    def _content_type_for(cls, model_or_content_type):
        if isinstance(model_or_content_type, ContentType):
            return model_or_content_type
        if model_or_content_type is None:
            return None
        model_class = getattr(model_or_content_type, "__class__", None)
        if isinstance(model_or_content_type, type):
            model_class = model_or_content_type
        try:
            return ContentType.objects.get_for_model(
                model_class, for_concrete_model=False
            )
        except Exception:
            return None

    @classmethod
    def get_cached_value(
        cls, model_or_content_type, builder: Callable[[], object], *, force_refresh=False
    ) -> object:
        content_type = cls._content_type_for(model_or_content_type)
        if content_type is None:
            return builder()

        return super().get_cached_value(
            content_type.pk, builder, force_refresh=force_refresh
        )

    @classmethod
    def invalidate_model_cache(cls, model_or_content_type):
        content_type = cls._content_type_for(model_or_content_type)
        if content_type is None:
            return
        cls.invalidate_cached_value(content_type.pk)


class DashboardRuleManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class BadgeCounter(StoredCounter):
    """Configurable badge counters for the admin dashboard."""

    cache_prefix = _BADGE_COUNTER_CACHE_PREFIX
    cache_timeout = None

    class ValueSource(models.TextChoices):
        SIGIL_TEXT = "sigil", _("Sigil string")
        CALLABLE = "callable", _("Python callable")

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="badge_counters",
        verbose_name=_("model"),
    )
    name = models.CharField(max_length=150)
    label_template = models.CharField(
        max_length=255,
        blank=True,
        help_text=_(
            "Optional label template that can reference {name}, {primary}, {secondary}, "
            "and {separator}. Badge counters are intended for values that do not change "
            "often; remember to invalidate the cache after updates."
        ),
    )
    priority = models.PositiveIntegerField(
        default=0,
        help_text=_("Lower values appear first from left to right."),
    )
    separator = models.CharField(
        max_length=8,
        default="/",
        help_text=_("Symbol shown between counters when two values are present."),
    )
    primary_source_type = models.CharField(
        max_length=20,
        choices=ValueSource.choices,
        default=ValueSource.SIGIL_TEXT,
    )
    primary_source = models.CharField(
        max_length=255,
        help_text=_(
            "Value source expressed as [sigils] or a dotted callable path. Badge counters "
            "are intended for values that do not change often and may require manual "
            "cache invalidation."
        ),
    )
    secondary_source_type = models.CharField(
        max_length=20,
        choices=ValueSource.choices,
        blank=True,
        null=True,
    )
    secondary_source = models.CharField(max_length=255, blank=True)
    css_class = models.CharField(
        max_length=100,
        default="badge-counter",
        help_text=_("Additional CSS classes applied to the badge span."),
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("priority", "pk")
        verbose_name = _("Badge Counter")
        verbose_name_plural = _("Badge Counters")
        unique_together = ("content_type", "name")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.name} ({self.content_type})"

    def _invoke_callable(self, func):
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):  # pragma: no cover - signature edge cases
            signature = None

        if signature:
            parameters = list(signature.parameters.values())
            if len(parameters) == 0:
                return func()
            if len(parameters) == 1:
                return func(self)

        try:
            return func(self)
        except TypeError:
            return func()

    def _resolve_source(self, source: str, source_type: str | None):
        if not source:
            return None

        if source_type == self.ValueSource.CALLABLE:
            func = self._import_callable(source)
            if func is None:
                return None

            try:
                return self._invoke_callable(func)
            except Exception:
                logger.exception(
                    "Badge counter callable failed",
                    extra={"badge_id": self.pk, "callable": source},
                )
            return None

        from apps.sigils.sigil_resolver import resolve_sigils

        return resolve_sigils(source)

    def _import_callable(self, source: str):
        last_error: Exception | None = None
        attempts = [source]

        legacy_source = self._legacy_callable_source(source)
        if legacy_source:
            attempts.append(legacy_source)

        for path in attempts:
            try:
                return import_string(path)
            except Exception as exc:  # pragma: no cover - runtime import errors
                last_error = exc
                continue

        logger.exception(
            "Unable to import badge counter callable",
            extra={"badge_id": self.pk, "callable": attempts[-1]},
            exc_info=last_error,
        )
        return None

    @staticmethod
    def _legacy_callable_source(source: str) -> str | None:
        if source.startswith("apps.") or "." not in source:
            return None

        app_prefix, _, remainder = source.partition(".")
        if not remainder:
            return None

        local_apps = getattr(settings, "LOCAL_APPS", [])
        known_modules = {
            app.split(".", 1)[1] for app in local_apps if app.startswith("apps.")
        }

        if app_prefix in known_modules:
            return f"apps.{source}"

        return None

    def _format_label(self, primary, secondary):
        template = (self.label_template or "").strip()
        values = {
            "name": self.name,
            "primary": primary,
            "secondary": secondary,
            "separator": self.separator,
        }

        if template:
            try:
                return template.format(**values)
            except Exception:
                logger.exception(
                    "Badge counter label formatting failed", extra={"badge_id": self.pk}
                )

        if secondary is None:
            return _("%(name)s: %(primary)s") % {
                "name": self.name,
                "primary": primary,
            }

        return _("%(name)s: %(primary)s%(separator)s%(secondary)s") % {
            "name": self.name,
            "primary": primary,
            "secondary": secondary,
            "separator": values["separator"],
        }

    def _normalize_result(self, result, secondary_override):
        if result is None and secondary_override is None:
            return None

        primary = None
        secondary = None
        label = None

        if isinstance(result, BadgeCounterResult):
            primary = result.primary
            secondary = result.secondary
            label = result.label
        elif isinstance(result, dict):
            primary = result.get("primary")
            secondary = result.get("secondary")
            label = result.get("label")
        elif isinstance(result, (list, tuple)):
            primary = result[0] if result else None
            if len(result) > 1:
                secondary = result[1]
        else:
            primary = result

        if secondary_override is not None:
            secondary = secondary_override

        if primary is None:
            return None

        return BadgeCounterResult(primary=primary, secondary=secondary, label=label)

    def build_display(self):
        if not self.is_enabled:
            return None

        primary_value = self._resolve_source(
            self.primary_source, self.primary_source_type
        )
        secondary_value = None
        if self.secondary_source:
            secondary_type = self.secondary_source_type or self.primary_source_type
            secondary_value = self._resolve_source(
                self.secondary_source, secondary_type
            )

        normalized = self._normalize_result(primary_value, secondary_value)
        if normalized is None:
            return None

        label = normalized.label or self._format_label(
            normalized.primary, normalized.secondary
        )
        separator = self.separator or "/"
        css_class = f"badge-counter {self.css_class}" if self.css_class else "badge-counter"
        css_class = " ".join(css_class.split())

        return {
            "primary": str(normalized.primary),
            "secondary": None
            if normalized.secondary is None
            else str(normalized.secondary),
            "label": label,
            "separator": separator,
            "css_class": css_class,
        }


class DashboardRule(StoredCounter):
    """Rule configuration for admin dashboard model rows."""

    cache_prefix = _DASHBOARD_RULE_CACHE_PREFIX
    cache_timeout = None

    class Implementation(models.TextChoices):
        CONDITION = "condition", _("SQL + Sigil comparison")
        PYTHON = "python", _("Python callable")

    name = models.CharField(max_length=200, unique=True)
    content_type = models.OneToOneField(
        ContentType, on_delete=models.CASCADE, related_name="dashboard_rule"
    )
    implementation = models.CharField(
        max_length=20, choices=Implementation.choices, default=Implementation.PYTHON
    )
    condition = ConditionTextField(blank=True, default="")
    function_name = models.CharField(max_length=255, blank=True)
    success_message = models.CharField(
        max_length=200, default=DEFAULT_SUCCESS_MESSAGE
    )
    failure_message = models.CharField(max_length=500, blank=True)

    objects = DashboardRuleManager()

    class Meta:
        ordering = ["name"]
        verbose_name = _("Dashboard Rule")
        verbose_name_plural = _("Dashboard Rules")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    def clean(self):
        super().clean()
        errors = {}

        if self.implementation == self.Implementation.PYTHON:
            if not self.function_name:
                errors["function_name"] = _(
                    "Provide a handler name for Python-based rules."
                )
        else:
            if not (self.condition or "").strip():
                errors["condition"] = _(
                    "Provide a condition for SQL-based rules."
                )

        if errors:
            raise ValidationError(errors)

    def evaluate(self) -> dict[str, object]:
        if self.implementation == self.Implementation.PYTHON:
            handler = load_callable(self.function_name)
            if handler is None:
                return rule_failure(_("Rule handler is not configured."))

            try:
                with bind_rule_model(self.content_type.model_class()._meta.label_lower):
                    return handler()
            except Exception:
                logger.exception("Dashboard rule handler failed: %s", self.function_name)
                return rule_failure(_("Unable to evaluate dashboard rule."))

        condition_field = self._meta.get_field("condition")
        result = condition_field.evaluate(self)
        if result.passed:
            message = self.success_message or str(DEFAULT_SUCCESS_MESSAGE)
            return rule_success(message)

        message = self.failure_message or _("Rule condition not met.")
        if result.error:
            message = f"{message} ({result.error})"
        return rule_failure(message)


@receiver(post_save, sender=BadgeCounter)
@receiver(post_delete, sender=BadgeCounter)
def clear_badge_counter_cache(sender, instance: BadgeCounter, **_kwargs):
    BadgeCounter.invalidate_model_cache(instance.content_type)


@receiver(post_save, sender=DashboardRule)
@receiver(post_delete, sender=DashboardRule)
def clear_dashboard_rule_cache(sender, instance: DashboardRule, **_kwargs):
    DashboardRule.invalidate_model_cache(instance.content_type)
