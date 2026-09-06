import pytest

from django.apps import apps
from django.contrib.contenttypes.models import ContentType

from apps.emails.models import (
    EmailArtifact,
    EmailTransaction,
    EmailTransactionAttachment,
)


EMAIL_MODELS = (
    EmailArtifact,
    EmailTransaction,
    EmailTransactionAttachment,
)


def test_email_persistence_models_are_owned_by_emails_app():
    assert {model._meta.app_label for model in EMAIL_MODELS} == {"emails"}
    assert {model._meta.db_table for model in EMAIL_MODELS} == {
        "core_emailartifact",
        "core_emailtransaction",
        "core_emailtransactionattachment",
    }


def test_core_email_imports_are_compatibility_aliases():
    from apps.core.models import EmailArtifact as LegacyEmailArtifact
    from apps.core.models import EmailTransaction as LegacyEmailTransaction
    from apps.core.models import (
        EmailTransactionAttachment as LegacyEmailTransactionAttachment,
    )

    assert LegacyEmailArtifact is EmailArtifact
    assert LegacyEmailTransaction is EmailTransaction
    assert LegacyEmailTransactionAttachment is EmailTransactionAttachment


def test_core_registry_no_longer_owns_email_models():
    for model in EMAIL_MODELS:
        with pytest.raises(LookupError):
            apps.get_model("core", model.__name__)


@pytest.mark.django_db
def test_email_content_types_use_new_owner():
    for model in EMAIL_MODELS:
        content_type = ContentType.objects.get_for_model(model)
        assert content_type.app_label == "emails"
        assert content_type.model == model._meta.model_name
        assert not ContentType.objects.filter(
            app_label="core", model=model._meta.model_name
        ).exists()
