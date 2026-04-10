"""Language and localization settings."""

from .base import BASE_DIR

LANGUAGE_CODE = "en-us"

LANGUAGES = [
    ("en", "English"),
]

PARLER_DEFAULT_LANGUAGE_CODE = "en"

PARLER_LANGUAGES = {
    None: (
        {"code": "en"},
    ),
    "default": {
        "fallbacks": ["en"],
        "hide_untranslated": False,
    },
}

LOCALE_PATHS = [BASE_DIR / "apps" / "locale" / "locale"]
FORMAT_MODULE_PATH = ["config.formats"]
TIME_ZONE = "America/Monterrey"
USE_I18N = False
USE_TZ = True
