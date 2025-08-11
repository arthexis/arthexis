import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase

from awg.admin import CableSizeAdmin
from awg.models import CableSize


class CableSizeAdminListDisplayTests(SimpleTestCase):
    def test_list_display_shows_area_and_amps(self):
        admin = CableSizeAdmin(CableSize, AdminSite())
        assert "area_kcmil" in admin.list_display
        assert "amps_60c" in admin.list_display
