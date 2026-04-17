import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory

from apps.sigils.models import SigilRoot
from apps.sigils.sigil_builder import SUPPORTED_PIPELINE_ACTIONS, _sigil_builder_view


@pytest.mark.django_db
def test_sigil_builder_context_exposes_uppercase_actions_and_discoverability_metadata(settings):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    user_model = get_user_model()
    SigilRoot.objects.update_or_create(
        prefix="USR",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(user_model),
            "is_user_safe": True,
        },
    )

    user = user_model.objects.create_superuser(
        username="sigil-admin",
        email="sigil-admin@example.com",
        password="abc12345",
    )
    request = RequestFactory().get("/admin/sigil-builder/")
    request.user = user

    response = _sigil_builder_view(request)
    context = response.context_data

    assert context["supported_pipeline_actions"] == SUPPORTED_PIPELINE_ACTIONS
    assert context["example_roots"]
    assert context["example_actions"]
    assert context["example_contexts"]
    assert any(
        ":" in item["expression"] and "|" in item["expression"]
        for item in context["expression_examples"]
    )


@pytest.mark.django_db
def test_sigil_builder_policy_reference_includes_user_safe_roots(settings):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    user_model = get_user_model()
    SigilRoot.objects.update_or_create(
        prefix="USR",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(user_model),
            "is_user_safe": True,
        },
    )

    user = user_model.objects.create_superuser(
        username="sigil-policy-admin",
        email="sigil-policy-admin@example.com",
        password="abc12345",
    )
    request = RequestFactory().get("/admin/sigil-builder/")
    request.user = user

    response = _sigil_builder_view(request)
    policy_rows = response.context_data["policy_reference"]
    user_safe_row = next(row for row in policy_rows if row["context"] == "user-safe")

    assert "USR" in user_safe_row["roots"]
    assert "FIELD" in user_safe_row["actions"]
