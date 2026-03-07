from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.translation import gettext as _

pytestmark = pytest.mark.django_db


def test_admin_index_hides_sidebar_widgets_for_staff_without_permissions(client):
    """Staff users without model permissions should not see dashboard widgets."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="staff-no-model-perms",
        password="unused",
        is_staff=True,
    )
    client.force_login(user)

    response = client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert _("You don't have permission to view or edit anything.") in content
    assert "id=\"admin-dashboard-widgets\"" not in content
    assert _("Configure Widgets") not in content
