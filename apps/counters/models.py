import logging
from collections.abc import Callable

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.locals.caches import CacheStoreMixin
from apps.sigils.fields import ConditionTextField

from .condition_structured import parse_boolean_literal, parse_decimal_literal
from .dashboard_rules import (
    DEFAULT_SUCCESS_MESSAGE,
    bind_rule_model,
    load_callable,
    rule_failure,
    rule_success,
)

logger = logging.getLogger(__name__)

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


class DashboardRule(StoredCounter):
    """Rule configuration for admin dashboard model rows."""

    cache_prefix = _DASHBOARD_RULE_CACHE_PREFIX
    cache_timeout = None

    class Implementation(models.TextChoices):
        CONDITION = "condition", _("SQL + Sigil comparison")
        PYTHON = "python", _("Python callable")

    class ConditionOperator(models.TextChoices):
        EQUAL = "=", _("Equals")
        GREATER_THAN = ">", _("Greater than")
        GREATER_THAN_OR_EQUAL = ">=", _("Greater than or equal")
        LESS_THAN = "<", _("Less than")
        LESS_THAN_OR_EQUAL = "<=", _("Less than or equal")
        NOT_EQUAL = "!=", _("Not equal")

    name = models.CharField(max_length=200, unique=True)
    content_type = models.OneToOneField(
        ContentType, on_delete=models.CASCADE, related_name="dashboard_rule"
    )
    implementation = models.CharField(
        max_length=20, choices=Implementation.choices, default=Implementation.PYTHON
    )
    condition = ConditionTextField(blank=True, default="")
    condition_operator = models.CharField(
        max_length=4,
        choices=ConditionOperator.choices,
        default=ConditionOperator.EQUAL,
    )
    condition_source = models.CharField(max_length=255, blank=True, default="")
    condition_expected_boolean = models.BooleanField(null=True, blank=True)
    condition_expected_number = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
    )
    condition_expected_text = models.CharField(max_length=255, blank=True, default="")
    condition_requires_triage = models.BooleanField(default=False)
    condition_triage_note = models.CharField(max_length=255, blank=True, default="")
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
            if self.condition_requires_triage:
                errors["condition"] = _(
                    "This condition must be migrated manually before it can run."
                )
            if not (self.condition_source or "").strip():
                errors["condition"] = _(
                    "Provide a structured condition source."
                )
            has_boolean = self.condition_expected_boolean is not None
            has_number = self.condition_expected_number is not None
            has_text = bool((self.condition_expected_text or "").strip())
            configured_types = [has_boolean, has_number, has_text]
            if sum(configured_types) != 1:
                errors["condition"] = _(
                    "Provide exactly one expected condition value type."
                )

        if errors:
            raise ValidationError(errors)

    def _resolve_condition_source(self) -> str:
        source = (self.condition_source or "").strip()
        if hasattr(self, "resolve_sigils_in_text"):
            return self.resolve_sigils_in_text(source)
        return source

    def _evaluate_structured_condition(self) -> tuple[bool, str | None]:
        source_text = (self._resolve_condition_source() or "").strip()
        if not source_text:
            return False, _("Condition source resolved to an empty value.")

        expected_boolean = self.condition_expected_boolean
        expected_number = self.condition_expected_number
        expected_text = (self.condition_expected_text or "").strip()
        operator = self.condition_operator

        if expected_boolean is not None:
            parsed_boolean = parse_boolean_literal(source_text)
            if parsed_boolean is None:
                return False, _("Condition source is not a boolean value.")
            return _compare_values(parsed_boolean, expected_boolean, operator), None

        if expected_number is not None:
            parsed_number = parse_decimal_literal(source_text)
            if parsed_number is None:
                return False, _("Condition source is not a numeric value.")
            return _compare_values(parsed_number, expected_number, operator), None

        return _compare_values(source_text, expected_text, operator), None

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

        if self.condition_requires_triage:
            message = self.failure_message or _("Rule condition requires manual triage.")
            triage_note = (self.condition_triage_note or "").strip()
            if triage_note:
                message = f"{message} ({triage_note})"
            return rule_failure(message)

        passed, error = self._evaluate_structured_condition()
        if passed:
            message = self.success_message or str(DEFAULT_SUCCESS_MESSAGE)
            return rule_success(
                message,
                is_default_message=(message == str(DEFAULT_SUCCESS_MESSAGE)),
            )

        message = self.failure_message or _("Rule condition not met.")
        if error:
            message = f"{message} ({error})"
        return rule_failure(message)


@receiver(post_save, sender=DashboardRule)
@receiver(post_delete, sender=DashboardRule)
def clear_dashboard_rule_cache(sender, instance: DashboardRule, **_kwargs):
    DashboardRule.invalidate_model_cache(instance.content_type)


def _compare_values(left, right, operator: str) -> bool:
    if operator == DashboardRule.ConditionOperator.EQUAL:
        return left == right
    if operator == DashboardRule.ConditionOperator.NOT_EQUAL:
        return left != right
    if operator == DashboardRule.ConditionOperator.GREATER_THAN:
        return left > right
    if operator == DashboardRule.ConditionOperator.GREATER_THAN_OR_EQUAL:
        return left >= right
    if operator == DashboardRule.ConditionOperator.LESS_THAN:
        return left < right
    if operator == DashboardRule.ConditionOperator.LESS_THAN_OR_EQUAL:
        return left <= right
    return False
