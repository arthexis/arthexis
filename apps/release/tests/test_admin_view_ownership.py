from __future__ import annotations

from apps.core.system import admin_views as core_admin_views
from apps.release import admin_views as release_admin_views


_RELEASE_VIEW_BY_ROUTE = {
    "system-upgrade-report": "_system_upgrade_report_view",
    "system-changelog-report": "_system_changelog_report_view",
    "system-changelog-data": "_system_changelog_report_data_view",
    "system-upgrade-run-check": "_system_trigger_upgrade_check_view",
    "system-upgrade-check-revision": "_system_upgrade_revision_check_view",
}


def test_release_owns_upgrade_and_changelog_task_panel_routes():
    routes = {route.name: route for route in core_admin_views.TASK_PANEL_ROUTES}

    for route_name, view_name in _RELEASE_VIEW_BY_ROUTE.items():
        owner_view = getattr(release_admin_views, view_name)
        assert routes[route_name].view is owner_view
        assert getattr(core_admin_views, view_name) is owner_view


def test_task_panel_route_names_remain_unique_after_release_handoff():
    names = [route.name for route in core_admin_views.TASK_PANEL_ROUTES]
    assert len(names) == len(set(names))
