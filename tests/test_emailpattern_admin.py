import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.urls import reverse


class EmailPatternAdminUrlTests(TestCase):
    def test_changelist_url_is_reversible(self):
        self.assertEqual(
            reverse("admin:post_office_emailpattern_changelist"),
            "/admin/post_office/emailpattern/",
        )
