"""Language and localization settings."""

LANGUAGE_CODE = "en"

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

FORMAT_MODULE_PATH = ["config.formats"]
TIME_ZONE = "America/Monterrey"
USE_I18N = False
USE_TZ = True
