from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def _available_language_codes() -> set[str]:
    return {code.lower() for code, _ in getattr(settings, "LANGUAGES", [])}


def default_report_language() -> str:
    configured = getattr(settings, "LANGUAGE_CODE", "en") or "en"
    configured = configured.replace("_", "-").lower()
    base = configured.split("-", 1)[0]
    available = _available_language_codes()
    if base in available:
        return base
    if configured in available:
        return configured
    if available:
        return next(iter(sorted(available)))
    return "en"


def normalize_report_language(language: str | None) -> str:
    default = default_report_language()
    if not language:
        return default
    candidate = str(language).strip().lower()
    if not candidate:
        return default
    if candidate.startswith(":"):
        return default
    if candidate.replace("-", "_") in _available_language_codes():
        return candidate.replace("-", "_")
    return default


def normalize_report_title(title: str | None) -> str:
    value = (title or "").strip()
    if "\r" in value or "\n" in value:
        raise ValidationError(
            _("Report title cannot contain control characters."),
        )
    return value
