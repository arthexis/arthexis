from django.contrib import admin

from apps.widgets.admin import WidgetAdmin
from apps.widgets.models import Widget


def test_widget_admin_registration_and_filters():
    model_admin = admin.site._registry.get(Widget)

    assert isinstance(model_admin, WidgetAdmin)
    assert model_admin.list_filter == ("zone", "is_enabled")
