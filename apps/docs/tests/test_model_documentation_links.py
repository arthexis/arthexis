from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.docs.models import ModelDocumentation


def _model_documentation_changelist_url() -> str:
    """Return the admin changelist URL for model documentation records."""

    return reverse("admin:docs_modeldocumentation_changelist")


def test_admin_changelist_shows_linked_documentation(admin_client):
    model_doc_type = ContentType.objects.get_for_model(ModelDocumentation, for_concrete_model=False)
    record = ModelDocumentation.objects.create(
        title="Modeling proposal",
        doc_path="docs/development/admin-ui-framework.md",
    )
    record.models.add(model_doc_type)

    response = admin_client.get(_model_documentation_changelist_url())

    assert response.status_code == 200
    assert "Linked documentation" in response.content.decode()
    assert record.title in response.content.decode()
    assert record.document_url() in response.content.decode()


def test_docs_page_shows_linked_admin_models_for_staff(client, admin_user):
    model_doc_type = ContentType.objects.get_for_model(ModelDocumentation, for_concrete_model=False)
    record = ModelDocumentation.objects.create(
        title="Modeling proposal",
        doc_path="docs/development/admin-ui-framework.md",
    )
    record.models.add(model_doc_type)

    client.force_login(admin_user)
    response = client.get(reverse("docs:docs-document", args=["development/admin-ui-framework.md"]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Configure linked models" in body
    assert _model_documentation_changelist_url() in body
    assert "Configure model docs.modeldocumentation in the admin console" in body
