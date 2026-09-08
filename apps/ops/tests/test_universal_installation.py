from django.conf import settings


def test_ops_remains_universally_installed_during_admin_notice_move():
    assert "apps.ops" in settings.PROJECT_LOCAL_APPS
    assert "apps.ops" in settings.INSTALLED_APPS
