import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, TestCase, override_settings

from config.settings import _candidate_origin_tuples, _get_allowed_hosts


class CSRFOriginSubnetTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _build_middleware_request(self, **request_kwargs):
        request = self.factory.post("/", **request_kwargs)
        request.META.setdefault("HTTP_HOST", "disallowed.test")
        return request

    def test_forwarded_https_origin_within_allowed_subnet(self):
        request = self._build_middleware_request(
            secure=True,
            HTTP_HOST="invalid-proxy.local",
        )
        request.META.update(
            {
                "HTTP_ORIGIN": "https://192.168.129.10:4443",
                "HTTP_X_FORWARDED_HOST": "192.168.129.20:4443",
                "HTTP_FORWARDED": 'for="192.168.129.30";proto=https;host="192.168.129.20:4443"',
            }
        )

        middleware = CsrfViewMiddleware(lambda r: None)

        allowed_hosts = _get_allowed_hosts()
        candidates = _candidate_origin_tuples(request, allowed_hosts)
        self.assertIn(("https", "192.168.129.20", "4443"), candidates)
        self.assertTrue(middleware._origin_verified(request))

    def test_forwarded_host_outside_allowed_hosts_is_rejected(self):
        request = self._build_middleware_request(
            secure=True,
            HTTP_HOST="invalid-proxy.local",
        )
        request.META.update(
            {
                "HTTP_ORIGIN": "https://203.0.113.10:4443",
                "HTTP_X_FORWARDED_HOST": "203.0.113.10:4443",
                "HTTP_FORWARDED": 'proto=https;host="203.0.113.10:4443"',
            }
        )

        middleware = CsrfViewMiddleware(lambda r: None)

        allowed_hosts = _get_allowed_hosts()
        candidates = _candidate_origin_tuples(request, allowed_hosts)
        self.assertEqual(candidates, [])
        self.assertFalse(middleware._origin_verified(request))

    @override_settings(ALLOWED_HOSTS=_get_allowed_hosts() + ["2001:db8::/32"])
    def test_ipv6_forwarded_host_matches_allowed_subnet(self):
        request = self._build_middleware_request(secure=True)
        request.META.update(
            {
                "HTTP_ORIGIN": "https://[2001:db8::1]:4443",
                "HTTP_X_FORWARDED_HOST": "[2001:db8::2]:4443",
                "HTTP_FORWARDED": 'for="[2001:db8::3]";proto=https;host="[2001:db8::2]:4443"',
            }
        )

        middleware = CsrfViewMiddleware(lambda r: None)

        allowed_hosts = _get_allowed_hosts()
        candidates = _candidate_origin_tuples(request, allowed_hosts)
        self.assertIn(("https", "2001:db8::2", "4443"), candidates)
        self.assertTrue(middleware._origin_verified(request))
