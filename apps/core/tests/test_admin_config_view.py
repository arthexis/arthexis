from __future__ import annotations

from django.urls import reverse

import pytest


@pytest.mark.integration
@pytest.mark.django_db
def test_admin_config_view_renders_section_jump_links(admin_client):
    response = admin_client.get(reverse("admin:config"))

    assert response.status_code == 200
    assert '<ul class="config-section-links"' in response.rendered_content
    assert 'href="#config-section-1"' in response.rendered_content
