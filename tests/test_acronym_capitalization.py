import os
import sys
import inspect
import re
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.apps import apps
from django.conf import settings
from django.test import TestCase


class AcronymCapitalizationTests(TestCase):
    def _check_text(self, text, location):
        text = text or ""
        for acronym in getattr(settings, "ACRONYMS", []):
            pattern = re.compile(rf"\b{re.escape(acronym)}s?\b", flags=re.IGNORECASE)
            for match in pattern.finditer(text):
                expected = acronym + ("s" if match.group(0).endswith("s") else "")
                self.assertEqual(
                    match.group(0),
                    expected,
                    f"{location} '{text}' does not capitalize acronym '{acronym}'",
                )

    def test_acronyms_capitalized(self):
        for app_label in getattr(settings, "LOCAL_APPS", []):
            config = apps.get_app_config(app_label)
            for model in config.get_models():
                for acronym in getattr(settings, "ACRONYMS", []):
                    pattern = re.compile(
                        rf"{re.escape(acronym)}s?", flags=re.IGNORECASE
                    )
                    for match in pattern.finditer(model.__name__):
                        # Skip matches that are part of a larger lowercase word
                        if (
                            match.start() > 0
                            and model.__name__[match.start() - 1].islower()
                        ) or (
                            match.end() < len(model.__name__)
                            and model.__name__[match.end()].islower()
                        ):
                            continue
                        expected = acronym + (
                            "s" if match.group(0).endswith("s") else ""
                        )
                        self.assertEqual(
                            match.group(0),
                            expected,
                            f"Model name '{model.__name__}' does not capitalize acronym '{acronym}'",
                        )
                doc = inspect.getdoc(model)
                if doc and not doc.startswith(f"{model.__name__}("):
                    self._check_text(doc, f"{model.__name__} docstring")
                for attr in ("verbose_name", "verbose_name_plural"):
                    self._check_text(
                        str(getattr(model._meta, attr)), f"{model.__name__} {attr}"
                    )
                for field in model._meta.get_fields():
                    if hasattr(field, "verbose_name"):
                        self._check_text(
                            str(field.verbose_name),
                            f"{model.__name__}.{field.name} verbose_name",
                        )
                    help_text = getattr(field, "help_text", "")
                    if help_text:
                        self._check_text(
                            str(help_text), f"{model.__name__}.{field.name} help_text"
                        )
                    description = getattr(field, "description", "")
                    if description:
                        self._check_text(
                            str(description),
                            f"{model.__name__}.{field.name} description",
                        )
