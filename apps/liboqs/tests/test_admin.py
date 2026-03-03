"""Regression tests for liboqs admin integration."""

import pytest
from django.urls import reverse

from apps.liboqs.models import LiboqsProfile


@pytest.mark.django_db
def test_liboqs_profile_admin_changelist_renders(admin_client):
    """Regression: liboqs profile changelist can be loaded in admin."""

    LiboqsProfile.objects.create(
        slug="default-kyber",
        display_name="Default Kyber",
        kem_algorithm="Kyber768",
        signature_algorithm="Dilithium2",
    )

    response = admin_client.get(reverse("admin:liboqs_liboqsprofile_changelist"))

    assert response.status_code == 200
    assert b"Default Kyber" in response.content
