"""Compatibility template tag shim for split admin templatetag modules."""

from django import template

from .admin_dashboard import (
    dashboard_model_status,
    dashboard_model_status_map,
    get_status,
    last_net_message,
    model_admin_actions,
    related_admin_models,
)
from .admin_styles import render_admin_stylesheets
from .admin_urls import (
    admin_changelist_url,
    admin_profile_url,
    admin_translate_url,
    optional_url,
    safe_admin_url,
    user_data_toggle_url,
)
from .admin_utils import (
    admin_show_filters,
    celery_feature_enabled,
    list_index,
    supports_user_datum,
)

register = template.Library()

register.simple_tag(render_admin_stylesheets, takes_context=True)
register.simple_tag(safe_admin_url)
register.simple_tag(admin_profile_url, takes_context=True)
register.simple_tag(last_net_message)
register.simple_tag(admin_translate_url)
register.simple_tag(model_admin_actions, takes_context=True)
register.simple_tag(admin_changelist_url)
register.simple_tag(dashboard_model_status)
register.simple_tag(dashboard_model_status_map)
register.filter("get_status", get_status)
register.simple_tag(optional_url)
register.simple_tag(related_admin_models)
register.simple_tag(celery_feature_enabled, takes_context=True)
register.filter("supports_user_datum", supports_user_datum)
register.filter("list_index", list_index)
register.simple_tag(user_data_toggle_url)
register.simple_tag(admin_show_filters)

__all__ = [
    "admin_changelist_url",
    "admin_profile_url",
    "admin_show_filters",
    "admin_translate_url",
    "celery_feature_enabled",
    "dashboard_model_status",
    "dashboard_model_status_map",
    "get_status",
    "last_net_message",
    "list_index",
    "model_admin_actions",
    "optional_url",
    "related_admin_models",
    "render_admin_stylesheets",
    "safe_admin_url",
    "supports_user_datum",
    "user_data_toggle_url",
]
