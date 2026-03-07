"""Admin smoke tests for the classification app."""

from __future__ import annotations

from django.urls import reverse

import pytest


@pytest.mark.django_db
@pytest.mark.parametrize(
    "admin_url_name",
    [
        "admin:classification_classificationtag_changelist",
        "admin:classification_imageclassifiermodel_changelist",
        "admin:classification_trainingrun_changelist",
        "admin:classification_trainingsample_changelist",
        "admin:classification_productmodelassignment_changelist",
        "admin:classification_contentclassification_changelist",
    ],
)
def test_classification_admin_changelists_render(admin_client, admin_url_name):
    """Each classification model should be available in Django admin."""

    response = admin_client.get(reverse(admin_url_name))

    assert response.status_code == 200
