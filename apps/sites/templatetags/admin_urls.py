"""Admin URL-oriented template tags."""

from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from django import template
from django.apps import apps
from django.contrib import admin
from django.contrib.admin.utils import quote
from django.contrib.contenttypes.models import ContentType
from django.urls import NoReverseMatch, Resolver404, get_script_prefix, resolve, reverse
from django.utils.http import urlencode

register = template.Library()


@register.filter
def admin_urlname(value, arg):
    """Build an admin URL name from model options and action suffix."""

    return "admin:%s_%s_%s" % (value.app_label, value.model_name, arg)


@register.filter
def admin_urlquote(value):
    """Quote URL path values using Django admin quoting rules."""

    return quote(value)


@register.simple_tag(takes_context=True)
def add_preserved_filters(context, url, popup=False, to_field=None):
    """Append current admin preserved filters to the supplied URL."""

    opts = context.get("opts")
    preserved_filters = context.get("preserved_filters")
    preserved_qsl = context.get("preserved_qsl")

    parsed_url = list(urlsplit(url))
    parsed_qs = dict(parse_qsl(parsed_url[3]))
    merged_qs = {}

    if preserved_qsl:
        merged_qs.update(preserved_qsl)

    if opts and preserved_filters:
        preserved_filters = dict(parse_qsl(preserved_filters))

        match_url = "/%s" % unquote(url).partition(get_script_prefix())[2]
        try:
            match = resolve(match_url)
        except Resolver404:
            pass
        else:
            current_url = "%s:%s" % (match.app_name, match.url_name)
            changelist_url = "admin:%s_%s_changelist" % (
                opts.app_label,
                opts.model_name,
            )
            if (
                changelist_url == current_url
                and "_changelist_filters" in preserved_filters
            ):
                preserved_filters = dict(
                    parse_qsl(preserved_filters["_changelist_filters"])
                )

        merged_qs.update(preserved_filters)

    if popup:
        from django.contrib.admin.options import IS_POPUP_VAR

        merged_qs[IS_POPUP_VAR] = 1
    if to_field:
        from django.contrib.admin.options import TO_FIELD_VAR

        merged_qs[TO_FIELD_VAR] = to_field

    merged_qs.update(parsed_qs)

    parsed_url[3] = urlencode(merged_qs)
    return urlunsplit(parsed_url)


@register.simple_tag
def safe_admin_url(view_name: str, *args, **kwargs) -> str:
    """Reverse an admin URL and gracefully handle missing patterns."""

    try:
        return reverse(view_name, args=args, kwargs=kwargs)
    except NoReverseMatch:
        return ""


def _admin_model_instance(model_admin, request, user):
    """Return a user instance from the target admin model queryset when possible."""

    model = model_admin.model
    if isinstance(user, model):
        return user
    try:
        queryset = model_admin.get_queryset(request)
    except Exception:
        queryset = model._default_manager.all()
    try:
        return queryset.get(pk=user.pk)
    except model.DoesNotExist:
        return None


def _admin_has_access(model_admin, request, obj):
    """Return whether the current request can view or change the admin object."""

    if hasattr(model_admin, "has_view_or_change_permission"):
        if model_admin.has_view_or_change_permission(request, obj=obj):
            return True
    else:
        has_change = getattr(model_admin, "has_change_permission", None)
        if has_change and has_change(request, obj):
            return True
        has_view = getattr(model_admin, "has_view_permission", None)
        if has_view and has_view(request, obj):
            return True
    return False


def _admin_change_url(model, user):
    """Build the Django admin change URL for a user-like model instance."""

    opts = model._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[user.pk])


@register.simple_tag(takes_context=True)
def admin_profile_url(context, user) -> str:
    """Return the first accessible admin change URL for the given user."""

    request = context.get("request")
    if request is None or user is None or not getattr(user, "pk", None):
        return ""

    def model_profile_url(model):
        model_admin = admin.site._registry.get(model)
        if not model_admin:
            return ""

        obj = _admin_model_instance(model_admin, request, user)
        if obj is None:
            return ""

        if not _admin_has_access(model_admin, request, obj):
            return ""

        try:
            return _admin_change_url(model_admin.model, user)
        except NoReverseMatch:
            return ""

    teams_user = None
    try:
        teams_user = apps.get_model("teams", "User")
    except LookupError:
        pass

    if teams_user and teams_user in admin.site._registry:
        return model_profile_url(teams_user)

    candidate_models = (
        ("core", "User"),
        ("auth", "User"),
    )

    for app_label, model_name in candidate_models:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue

        url = model_profile_url(model)
        if url:
            return url

    return ""


@register.simple_tag
def admin_translate_url(language_tabs) -> str:
    """Return the first available translation URL for parler language tabs."""

    if not language_tabs:
        return ""

    for url, _name, _code, _status in language_tabs:
        if url:
            return url

    return ""


@register.simple_tag
def admin_changelist_url(ct: ContentType) -> str:
    """Return the admin changelist URL for the given content type."""

    try:
        return reverse(f"admin:{ct.app_label}_{ct.model}_changelist")
    except NoReverseMatch:
        return ""


@register.simple_tag
def optional_url(viewname: str, *args, **kwargs) -> str:
    """Return ``reverse(viewname)`` or an empty string when missing."""

    try:
        return reverse(viewname, args=args or None, kwargs=kwargs or None)
    except NoReverseMatch:
        return ""


@register.simple_tag
def user_data_toggle_url(cl, obj) -> str:
    """Return the admin URL that toggles user datum for ``obj``."""

    if not obj:
        return ""
    try:
        app_label = cl.opts.app_label
        model_name = cl.opts.model_name
    except AttributeError:
        return ""
    try:
        return reverse(
            "admin:user_data_toggle",
            args=(app_label, model_name, obj.pk),
        )
    except NoReverseMatch:
        return ""
