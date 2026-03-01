"""Internationalization and locale settings."""

from django.utils.translation import gettext_lazy as _

from .base import BASE_DIR

LANGUAGE_CODE = "en-us"

LANGUAGES = [
    ("es", _("Spanish (Latin America)")),
    ("en", _("English")),
    ("it", _("Italian")),
    ("de", _("German")),
]

PARLER_DEFAULT_LANGUAGE_CODE = "en"

PARLER_LANGUAGES = {
    None: (
        {"code": "en"},
        {"code": "es"},
        {"code": "it"},
        {"code": "de"},
    ),
    "default": {
        "fallbacks": ["en"],
        "hide_untranslated": False,
    },
}

LOCALE_PATHS = [BASE_DIR / "apps" / "locale" / "locale"]
FORMAT_MODULE_PATH = ["config.formats"]
TIME_ZONE = "America/Monterrey"
USE_I18N = True
USE_TZ = True
