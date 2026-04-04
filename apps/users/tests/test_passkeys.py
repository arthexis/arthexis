"""Tests for passkey helper utilities."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from apps.users import passkeys



@pytest.mark.critical
def test_expected_origins_uses_validated_request_host_only():
    """Expected origins should not trust unvalidated forwarding/origin headers."""

    request = RequestFactory().get(
        "/",
        HTTP_HOST="localhost",
        HTTP_ORIGIN="https://evil.example",
        HTTP_REFERER="https://evil.example/login",
    )

    origins = passkeys._expected_origins(request)

    assert origins == ["http://localhost"]
