import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.test import SimpleTestCase, override_settings
from core.system import _gather_info


class SystemInfoRoleTests(SimpleTestCase):
    @override_settings(NODE_ROLE="Terminal")
    def test_defaults_to_terminal(self):
        info = _gather_info()
        self.assertEqual(info["role"], "Terminal")

    @override_settings(NODE_ROLE="Satellite")
    def test_uses_settings_role(self):
        info = _gather_info()
        self.assertEqual(info["role"], "Satellite")
