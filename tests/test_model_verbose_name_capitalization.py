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
    def test_model_verbose_names_capitalized(self):
        for app_label in getattr(settings, "LOCAL_APPS", []):
            config = apps.get_app_config(app_label)
            for model in config.get_models():
                for attr in ("verbose_name", "verbose_name_plural"):
                    name = str(getattr(model._meta, attr))
                    if " " in name:
                        for word in name.split():
                            if word and word[0].isalpha():
                                self.assertEqual(
                                    word[0],
                                    word[0].upper(),
                                    f"{model.__name__} {attr} '{name}' is not capitalized",
                                )
