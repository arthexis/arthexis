from apps.widgets.admin import WidgetAdmin


def test_widget_admin_filters_hide_required_feature_filter():
    assert WidgetAdmin.list_filter == ("zone", "is_enabled")
