from __future__ import annotations

from django.urls import reverse

import pytest

from apps.core import environment


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("config_sections", "expected_jump_links"),
    [
        ([{"name": "General", "settings": [("DEBUG", "False")]}], True),
        ([], False),
    ],
)
def test_admin_config_view_renders_section_jump_links(
    admin_client, monkeypatch, config_sections, expected_jump_links
):
    monkeypatch.setattr(environment, "_group_django_settings", lambda _: config_sections)
    response = admin_client.get(reverse("admin:config"))
    content = response.content.decode()

    assert response.status_code == 200
    assert ('<ul class="config-section-links"' in content) is expected_jump_links
    assert ('href="#config-section-1"' in content) is expected_jump_links
