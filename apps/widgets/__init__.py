"""Widget registration and rendering utilities."""

default_app_config = "apps.widgets.apps.WidgetsConfig"

from .registry import WidgetDefinition, WidgetMixin, register_widget

__all__ = ["WidgetDefinition", "WidgetMixin", "register_widget"]
