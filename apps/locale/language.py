from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

_DEFAULT_REPORT_LANGUAGE = "en"


def default_report_language() -> str:
    return _DEFAULT_REPORT_LANGUAGE


def normalize_report_language(language: str | None) -> str:
    if not language:
        return _DEFAULT_REPORT_LANGUAGE
    candidate = str(language).strip().lower().replace("_", "-")
    if candidate == _DEFAULT_REPORT_LANGUAGE or candidate.startswith(
        f"{_DEFAULT_REPORT_LANGUAGE}-"
    ):
        return _DEFAULT_REPORT_LANGUAGE
    return _DEFAULT_REPORT_LANGUAGE


def normalize_report_title(title: str | None) -> str:
    value = (title or "").strip()
    if "\r" in value or "\n" in value:
        raise ValidationError(
            _("Report title cannot contain control characters."),
        )
    return value


__all__ = [
    "default_report_language",
    "normalize_report_language",
    "normalize_report_title",
]
