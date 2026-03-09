from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.docs.models import ModelDocumentation


def test_admin_changelist_shows_linked_documentation(admin_client):
    user_type = ContentType.objects.get_for_model(get_user_model())
    record = ModelDocumentation.objects.create(
        title="Modeling proposal",
        doc_path="docs/modeling-system-proposal.md",
    )
    record.models.add(user_type)

    response = admin_client.get(reverse("admin:auth_user_changelist"))

    assert response.status_code == 200
    assert "Linked documentation" in response.content.decode()
    assert record.title in response.content.decode()
    assert record.document_url() in response.content.decode()


def test_docs_page_shows_linked_admin_models_for_staff(admin_client):
    user_type = ContentType.objects.get_for_model(get_user_model())
    record = ModelDocumentation.objects.create(
        title="Modeling proposal",
        doc_path="docs/modeling-system-proposal.md",
    )
    record.models.add(user_type)

    response = admin_client.get(reverse("docs:docs-document", args=["modeling-system-proposal.md"]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Configure linked models" in body
    assert reverse("admin:auth_user_changelist") in body
    assert "Configure model auth.user in the admin console" in body
