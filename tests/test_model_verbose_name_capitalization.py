import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.apps import apps
from django.conf import settings
from django.test import TestCase


class ModelVerboseNameCapitalizationTests(TestCase):
    BRAND_TITLE_CASE_EXCEPTIONS = {"OpenPay"}

    def _iter_model_verbose_names(self):
        for app_label in getattr(settings, "LOCAL_APPS", []):
            config = apps.get_app_config(app_label)
            for model in config.get_models():
                if ".migrations." in model.__module__:
                    continue
                for attr in ("verbose_name", "verbose_name_plural"):
                    yield model, attr, str(getattr(model._meta, attr))

    @staticmethod
    def _split_words(name: str) -> list[str]:
        segments = [name]
        for separator in (" ", "-", "/"):
            segments = [part for segment in segments for part in segment.split(separator)]
        return [word for word in segments if any(char.isalpha() for char in word)]

    def _is_title_case_word(self, word: str) -> bool:
        if not word:
            return True
        if word.isupper():
            return True
        lowered = word.lower()
        for suffix in ("s", "es"):
            if lowered.endswith(suffix) and word[: -len(suffix)].isupper():
                return True
        if "-" in word:
            return all(self._is_title_case_word(part) for part in word.split("-"))
        if word in self.BRAND_TITLE_CASE_EXCEPTIONS:
            return True
        return word[0].isupper() and word[1:].islower()

    def test_model_verbose_names_capitalized(self):
        for model, attr, name in self._iter_model_verbose_names():
            if " " in name:
                for word in name.split():
                    if word and word[0].isalpha():
                        self.assertEqual(
                            word[0],
                            word[0].upper(),
                            f"{model.__name__} {attr} '{name}' is not capitalized",
                        )

    def test_model_verbose_names_use_title_case(self):
        for model, attr, name in self._iter_model_verbose_names():
            for word in self._split_words(name):
                self.assertTrue(
                    self._is_title_case_word(word),
                    f"{model.__name__} {attr} '{name}' must use Title Case words",
                )
