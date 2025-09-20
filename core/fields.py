from dataclasses import dataclass
import re
import sqlite3

from django.db import models
from django.db.models.fields import DeferredAttribute
from django.utils.translation import gettext_lazy as _


class _BaseSigilDescriptor(DeferredAttribute):
    def __set__(self, instance, value):
        instance.__dict__[self.field.attname] = value


class _CheckSigilDescriptor(_BaseSigilDescriptor):
    def __get__(self, instance, cls=None):
        value = super().__get__(instance, cls)
        if instance is None:
            return value
        if getattr(instance, f"{self.field.name}_resolve_sigils", False):
            return instance.resolve_sigils(self.field.name)
        return value


class _AutoSigilDescriptor(_BaseSigilDescriptor):
    def __get__(self, instance, cls=None):
        value = super().__get__(instance, cls)
        if instance is None:
            return value
        return instance.resolve_sigils(self.field.name)


class _SigilBaseField:
    def value_from_object(self, obj):
        return obj.__dict__.get(self.attname)

    def pre_save(self, model_instance, add):
        # ``models.Field.pre_save`` uses ``getattr`` which would resolve the
        # sigil descriptor. Persist the raw database value instead so env-based
        # placeholders remain intact when editing through admin forms.
        return self.value_from_object(model_instance)


class SigilCheckFieldMixin(_SigilBaseField):
    descriptor_class = _CheckSigilDescriptor

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name, private_only=private_only)
        extra_name = f"{name}_resolve_sigils"
        if not any(f.name == extra_name for f in cls._meta.fields):
            cls.add_to_class(
                extra_name,
                models.BooleanField(
                    default=False,
                    verbose_name="Resolve [SIGILS] in templates",
                ),
            )


class SigilAutoFieldMixin(_SigilBaseField):
    descriptor_class = _AutoSigilDescriptor

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name, private_only=private_only)


class SigilShortCheckField(SigilCheckFieldMixin, models.CharField):
    pass


class SigilLongCheckField(SigilCheckFieldMixin, models.TextField):
    pass


class SigilShortAutoField(SigilAutoFieldMixin, models.CharField):
    pass


class SigilLongAutoField(SigilAutoFieldMixin, models.TextField):
    pass


class ConditionEvaluationError(Exception):
    """Raised when a condition expression cannot be evaluated."""


@dataclass
class ConditionCheckResult:
    """Represents the outcome of evaluating a condition field."""

    passed: bool
    resolved: str
    error: str | None = None


_COMMENT_PATTERN = re.compile(r"(--|/\*)")
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(ATTACH|DETACH|ALTER|ANALYZE|CREATE|DROP|INSERT|UPDATE|DELETE|REPLACE|"
    r"VACUUM|TRIGGER|TABLE|INDEX|VIEW|PRAGMA|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|WITH)\b",
    re.IGNORECASE,
)


def _evaluate_sql_condition(expression: str) -> bool:
    """Evaluate a SQL expression in an isolated SQLite connection."""

    if ";" in expression:
        raise ConditionEvaluationError(
            _("Semicolons are not allowed in conditions."),
        )
    if _COMMENT_PATTERN.search(expression):
        raise ConditionEvaluationError(
            _("SQL comments are not allowed in conditions."),
        )
    match = _FORBIDDEN_KEYWORDS.search(expression)
    if match:
        raise ConditionEvaluationError(
            _("Disallowed keyword in condition: %(keyword)s")
            % {"keyword": match.group(1)},
        )

    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.enable_load_extension(False)
            except AttributeError:
                # ``enable_load_extension`` is not available on some platforms.
                pass
            cursor = conn.execute(
                f"SELECT CASE WHEN ({expression}) THEN 1 ELSE 0 END"
            )
            row = cursor.fetchone()
            return bool(row[0]) if row else False
        finally:
            conn.close()
    except sqlite3.Error as exc:  # pragma: no cover - exact error message varies
        raise ConditionEvaluationError(str(exc)) from exc


class ConditionTextField(models.TextField):
    """Field storing a conditional SQL expression resolved through [sigils]."""

    def evaluate(self, instance) -> ConditionCheckResult:
        """Evaluate the stored expression for ``instance``."""

        value = self.value_from_object(instance)
        if hasattr(instance, "resolve_sigils"):
            resolved = instance.resolve_sigils(self.name)
        else:
            resolved = value

        if resolved is None:
            resolved_text = ""
        else:
            resolved_text = str(resolved)

        resolved_text = resolved_text.strip()
        if not resolved_text:
            return ConditionCheckResult(True, resolved_text)

        try:
            passed = _evaluate_sql_condition(resolved_text)
            return ConditionCheckResult(passed, resolved_text)
        except ConditionEvaluationError as exc:
            return ConditionCheckResult(False, resolved_text, str(exc))
