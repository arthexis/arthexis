import importlib

import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType

from apps.ops.admin_notice import AdminNotice
from apps.ops.admin_notice_admin import AdminNoticeAdmin


def test_admin_notice_is_owned_by_ops_without_renaming_table():
    assert AdminNotice._meta.app_label == "ops"
    assert AdminNotice._meta.db_table == "core_adminnotice"
    assert AdminNotice._meta.permissions == []


def test_core_admin_notice_import_is_compatibility_alias():
    from apps.core.models import AdminNotice as LegacyAdminNotice

    assert LegacyAdminNotice is AdminNotice
    assert importlib.import_module("apps.core.models.admin_notice") is importlib.import_module(
        "apps.ops.admin_notice"
    )


def test_admin_notice_admin_is_registered_by_ops():
    assert isinstance(admin.site._registry[AdminNotice], AdminNoticeAdmin)
    assert type(admin.site._registry[AdminNotice]).__module__ == "apps.ops.admin_notice_admin"


def test_core_registry_no_longer_owns_admin_notice():
    with pytest.raises(LookupError):
        apps.get_model("core", "AdminNotice")


@pytest.mark.django_db
def test_admin_notice_content_type_uses_ops_owner():
    content_type = ContentType.objects.get_for_model(AdminNotice)
    assert content_type.app_label == "ops"
    assert content_type.model == "adminnotice"
    assert not ContentType.objects.filter(app_label="core", model="adminnotice").exists()
