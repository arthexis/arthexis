from django.apps import apps
from django.test import SimpleTestCase


class RetiredApiExplorerTests(SimpleTestCase):
    def test_generic_explorer_models_are_not_registered(self):
        api_models = apps.get_app_config("apis").models

        self.assertNotIn("apiexplorer", api_models)
        self.assertNotIn("resourcemethod", api_models)
