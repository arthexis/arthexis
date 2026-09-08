import importlib

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType

from apps.sites.models import InviteLead, LeadBase


def test_invite_lead_is_owned_by_sites_without_renaming_table():
    assert InviteLead._meta.app_label == "pages"
    assert InviteLead._meta.db_table == "core_invitelead"
    assert InviteLead._meta.get_field("assign_to").remote_field.related_name == (
        "core_invitelead_assignments"
    )
    assert LeadBase._meta.abstract is True


def test_core_lead_imports_are_compatibility_aliases():
    from apps.core.models import InviteLead as LegacyInviteLead
    from apps.core.models import LeadBase as LegacyLeadBase

    assert LegacyInviteLead is InviteLead
    assert LegacyLeadBase is LeadBase
    assert importlib.import_module("apps.core.models.invite_lead") is importlib.import_module(
        "apps.sites.models.invite_lead"
    )
    assert importlib.import_module("apps.core.models.lead_base") is importlib.import_module(
        "apps.sites.models.lead_base"
    )


def test_core_registry_no_longer_owns_invite_lead():
    with pytest.raises(LookupError):
        apps.get_model("core", "InviteLead")


@pytest.mark.django_db
def test_invite_lead_content_type_uses_pages_owner():
    content_type = ContentType.objects.get_for_model(InviteLead)
    assert content_type.app_label == "pages"
    assert content_type.model == "invitelead"
    assert not ContentType.objects.filter(app_label="core", model="invitelead").exists()
