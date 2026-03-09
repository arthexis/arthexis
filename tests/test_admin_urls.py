"""Regression tests for admin URL routing aliases."""

from __future__ import annotations

from django.test import override_settings


@override_settings(ADMIN_URL_PATH="control-panel/")
def test_admin_alias_redirects_to_configured_mount(client, db):
    """`/admin/` should redirect to the configured admin mount path."""

    response = client.get("/admin/", follow=False)

    assert response.status_code == 302
    assert response.headers["Location"] == "/control-panel/"
