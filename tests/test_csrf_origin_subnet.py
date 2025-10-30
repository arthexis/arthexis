import os
import sys
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, TestCase
from django.test.utils import override_settings


class CSRFOriginSubnetTests(TestCase):
    def test_origin_in_allowed_subnet(self):
        rf = RequestFactory()
        request = rf.post("/", HTTP_HOST="192.168.129.10:8888")
        request.META["HTTP_ORIGIN"] = "http://192.168.129.10:8000"
        middleware = CsrfViewMiddleware(lambda r: None)
        self.assertTrue(middleware._origin_verified(request))

    @override_settings(ALLOWED_HOSTS=["testserver", "192.168.0.0/16", "10.42.0.0/16"])
    def test_check_referer_permits_forwarded_host_within_subnet(self):
        rf = RequestFactory()
        request = rf.post("/", secure=True)
        request.META.update(
            {
                "HTTP_REFERER": "https://192.168.129.20/dashboard",
                "HTTP_X_FORWARDED_HOST": "192.168.129.10:443",
                "HTTP_X_FORWARDED_PROTO": "https",
                "HTTP_FORWARDED": 'proto=https;host="192.168.129.10:443"',
            }
        )
        middleware = CsrfViewMiddleware(lambda r: None)

        with mock.patch("config.settings._original_check_referer", side_effect=AssertionError("fallback")) as fallback:
            middleware._check_referer(request)
        fallback.assert_not_called()

    @override_settings(ALLOWED_HOSTS=["testserver", "192.168.0.0/16", "10.42.0.0/16"])
    def test_check_referer_mismatched_subnet_falls_back(self):
        rf = RequestFactory()
        request = rf.post("/", secure=True)
        request.META.update(
            {
                "HTTP_REFERER": "https://192.168.129.20",
                "HTTP_X_FORWARDED_HOST": "10.42.10.5:443",
                "HTTP_X_FORWARDED_PROTO": "https",
                "HTTP_FORWARDED": 'proto=https;host="10.42.10.5:443"',
            }
        )
        middleware = CsrfViewMiddleware(lambda r: None)

        with mock.patch("config.settings._original_check_referer", side_effect=RuntimeError("fallback")) as fallback:
            with self.assertRaises(RuntimeError):
                middleware._check_referer(request)
        fallback.assert_called_once()

    @override_settings(ALLOWED_HOSTS=["testserver", "192.168.0.0/16"])
    def test_check_referer_rejects_http_scheme(self):
        rf = RequestFactory()
        request = rf.post("/", secure=False)
        request.META.update(
            {
                "HTTP_REFERER": "http://192.168.129.20/profile",
                "HTTP_X_FORWARDED_HOST": "192.168.129.10:80",
                "HTTP_X_FORWARDED_PROTO": "http",
                "HTTP_FORWARDED": 'proto=http;host="192.168.129.10:80"',
            }
        )
        middleware = CsrfViewMiddleware(lambda r: None)

        with mock.patch("config.settings._original_check_referer", side_effect=RuntimeError("fallback")) as fallback:
            with self.assertRaises(RuntimeError):
                middleware._check_referer(request)
        fallback.assert_called_once()
