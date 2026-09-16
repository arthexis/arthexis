from config.settings.apps import (
    PROJECT_LOCAL_APPS,
    _resolve_installed_app_entries,
)
from config.settings.nginx_retirement import (
    MIGRATION_ONLY_APPS,
    apply_nginx_runtime_retirement,
)


def test_nginx_is_not_a_project_local_runtime_app():
    assert "apps.nginx" not in PROJECT_LOCAL_APPS
    assert MIGRATION_ONLY_APPS == ("apps.nginx",)


def test_runtime_app_resolution_cannot_reintroduce_nginx():
    resolved = _resolve_installed_app_entries(
        node_role="watchtower",
        profile_enabled=False,
        enabled_app_lock_entries=("apps.nginx", "apps.core"),
    )

    assert "apps.nginx" not in resolved


def test_migration_retirement_hook_can_still_load_nginx_shell():
    installed_apps = ["apps.core"]

    apply_nginx_runtime_retirement(installed_apps, argv=("migrate",))

    assert "apps.nginx" in installed_apps
