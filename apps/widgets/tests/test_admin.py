from apps.widgets.admin import WidgetAdmin


def test_widget_admin_list_filters_excludes_required_feature():
    assert WidgetAdmin.list_filter == ("zone", "is_enabled")
