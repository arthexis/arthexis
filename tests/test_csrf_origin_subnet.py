import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, TestCase


class CSRFOriginSubnetTests(TestCase):
    def test_origin_in_allowed_subnet(self):
        rf = RequestFactory()
        request = rf.post("/", HTTP_HOST="192.168.129.10:8888")
        request.META["HTTP_ORIGIN"] = "http://192.168.129.10:8000"
        middleware = CsrfViewMiddleware(lambda r: None)
        self.assertTrue(middleware._origin_verified(request))
