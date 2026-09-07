import pytest
from django.contrib.auth.models import Permission

from apps.release import admin_views
from apps.release.models import ReleasePermission


def test_upgrade_check_permission_is_release_owned_runtime_policy():
    assert admin_views.UPGRADE_CHECK_PERMISSION == "release.can_trigger_upgrade_checks"
    assert ReleasePermission._meta.managed is False
    assert ReleasePermission._meta.default_permissions == ()
    assert (
        "can_trigger_upgrade_checks",
        "Can trigger upgrade checks",
    ) in ReleasePermission._meta.permissions


@pytest.mark.django_db
def test_upgrade_check_permission_uses_release_content_type():
    permission = Permission.objects.get(codename="can_trigger_upgrade_checks")
    assert permission.content_type.app_label == "release"
    assert permission.content_type.model == "releasepermission"
    assert not Permission.objects.filter(
        content_type__app_label="core",
        content_type__model="adminnotice",
        codename="can_trigger_upgrade_checks",
    ).exists()
    assert not Permission.objects.filter(
        content_type__app_label="ops",
        content_type__model="adminnotice",
        codename="can_trigger_upgrade_checks",
    ).exists()


def test_release_permission_content_type_model_name_is_stable():
    assert ReleasePermission._meta.app_label == "release"
    assert ReleasePermission._meta.model_name == "releasepermission"
