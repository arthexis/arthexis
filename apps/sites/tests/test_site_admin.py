"""Tests for SiteProxy admin changelist behavior."""

from __future__ import annotations

import pytest
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.regression
def test_siteproxy_changelist_uses_short_column_names(admin_client):
    """Regression: SiteProxy changelist should display compact column headings."""

    response = admin_client.get(reverse("admin:pages_siteproxy_changelist"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Default" in content
    assert "Interface" in content
    assert "HTTPS" in content
    assert "Public chat" in content
    assert "Default landing" not in content
    assert "Interface landing" not in content
    assert "Require https" not in content
    assert "Enable public chat" not in content
