from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from django.apps import apps
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.translation import gettext as _

from apps.core.entity import Entity

from .core import (
    _apply_user_fixture_paths,
    _data_dir,
    _fixture_path,
    _load_fixture,
    _resolve_fixture_user,
    _supports_user_datum,
    _user_allows_user_data,
    _user_fixture_status,
    delete_user_fixture,
    dump_user_fixture,
)
from .fixtures import _seed_fixture_index, _seed_fixture_path
from .transfer import _safe_next_url



def _iter_entity_admin_models():
    """Yield registered :class:`Entity` admin models without proxy duplicates."""

    seen: set[type] = set()
    for model, model_admin in admin.site._registry.items():
        if not issubclass(model, Entity):
            continue
        concrete_model = model._meta.concrete_model
        if concrete_model in seen:
            continue
        seen.add(concrete_model)
        yield model, model_admin



def toggle_user_datum(request, app_label, model_name, object_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        model = apps.get_model(app_label, model_name)
    except LookupError as exc:  # pragma: no cover - defensive
        raise Http404 from exc

    if model is None or not _supports_user_datum(model):
        raise Http404

    model_admin = admin.site._registry.get(model)
    if model_admin is None:
        raise Http404

    try:
        pk = model._meta.pk.to_python(object_id)
    except (TypeError, ValueError, ValidationError) as exc:
        raise Http404 from exc

    queryset = model_admin.get_queryset(request)
    try:
        obj = queryset.get(pk=pk)
    except model.DoesNotExist as exc:
        raise Http404 from exc

    if not model_admin.has_change_permission(request, obj):
        raise PermissionDenied

    manager = getattr(model, "all_objects", model._default_manager)
    target_user = _resolve_fixture_user(obj, request.user)
    allow_user_data = _user_allows_user_data(target_user)
    message = None

    if obj.is_user_data:
        manager.filter(pk=obj.pk).update(is_user_data=False)
        obj.is_user_data = False
        delete_user_fixture(obj, target_user)
        handler = getattr(model_admin, "user_datum_deleted", None)
        if callable(handler):
            handler(request, obj)
        message = _("User datum removed.")
    elif allow_user_data:
        manager.filter(pk=obj.pk).update(is_user_data=True)
        obj.is_user_data = True
        dump_user_fixture(obj, target_user)
        handler = getattr(model_admin, "user_datum_saved", None)
        if callable(handler):
            handler(request, obj)
        path = _fixture_path(target_user, obj)
        message = _("User datum saved to %(path)s") % {"path": str(path)}
    else:
        messages.warning(
            request,
            _("User data is not available for this account."),
        )

    if message:
        try:
            model_admin.message_user(request, message)
        except Exception:  # pragma: no cover - defensive
            messages.success(request, message)

    next_url = _safe_next_url(request)
    if not next_url:
        try:
            next_url = reverse(f"admin:{app_label}_{model_name}_changelist")
        except NoReverseMatch:
            next_url = reverse("admin:index")

    return HttpResponseRedirect(next_url)



def _seed_data_view(request):
    sections = []
    fixture_index = _seed_fixture_index()
    for model, model_admin in _iter_entity_admin_models():
        objs = model.objects.filter(is_seed_data=True)
        if not objs.exists():
            continue
        items = []
        for obj in objs:
            url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
            fixture = _seed_fixture_path(obj, index=fixture_index)
            target_user = _resolve_fixture_user(obj, request.user)
            allow_user_data = _user_allows_user_data(target_user)
            custom = False
            if allow_user_data and target_user:
                custom = _fixture_path(target_user, obj).exists()
            items.append(
                {
                    "url": url,
                    "label": str(obj),
                    "fixture": fixture,
                    "custom": custom,
                }
            )
        sections.append({"opts": model._meta, "items": items})
    context = admin.site.each_context(request)
    context.update({"title": _("Seed Data"), "sections": sections})
    return TemplateResponse(request, "admin/data_list.html", context)



def _user_data_view(request):
    sections = []
    for model, model_admin in _iter_entity_admin_models():
        objs = model.objects.filter(is_user_data=True)
        if not objs.exists():
            continue
        items = []
        for obj in objs:
            url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
            fixture = _fixture_path(request.user, obj)
            items.append({"url": url, "label": str(obj), "fixture": fixture})
        sections.append({"opts": model._meta, "items": items})
    fixture_status = _user_fixture_status(request.user)
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("User Data"),
            "sections": sections,
            "import_export": True,
            "fixture_status": fixture_status,
        }
    )
    return TemplateResponse(request, "admin/data_list.html", context)



def _user_data_export(request):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zf:
        for path in _data_dir(request.user).glob("*.json"):
            zf.write(path, arcname=path.name)
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = (
        f"attachment; filename=user_data_{request.user.pk}.zip"
    )
    return response



def _user_data_import(request):
    if request.method == "POST" and request.FILES.get("data_zip"):
        with ZipFile(request.FILES["data_zip"]) as zf:
            paths = []
            data_dir = _data_dir(request.user)
            for name in zf.namelist():
            for name in zf.namelist():
                basename = name.replace('\\', '/').split('/')[-1]
                if not basename.endswith('.json') or not basename:
                    continue
                content = zf.read(name)
                target = data_dir / basename
                with target.open('wb') as f:
                    f.write(content)
                paths.append(target)
        if paths:
            for path in paths:
                _load_fixture(path)
    return HttpResponseRedirect(reverse("admin:user_data"))



def _user_data_apply_fixtures(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    status = _user_fixture_status(request.user)
    _apply_user_fixture_paths(
        request,
        status["pending"],
        action_label=_("Applied"),
        empty_message=_("No unapplied user data fixtures found."),
    )
    return HttpResponseRedirect(reverse("admin:user_data"))



def _user_data_reset_fixtures(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    status = _user_fixture_status(request.user)
    _apply_user_fixture_paths(
        request,
        status["total"],
        action_label=_("Reset"),
        empty_message=_("No user data fixtures found."),
    )
    return HttpResponseRedirect(reverse("admin:user_data"))



def patch_admin_user_data_views() -> None:
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("seed-data/", admin.site.admin_view(_seed_data_view), name="seed_data"),
            path("user-data/", admin.site.admin_view(_user_data_view), name="user_data"),
            path(
                "user-data/export/",
                admin.site.admin_view(_user_data_export),
                name="user_data_export",
            ),
            path(
                "user-data/import/",
                admin.site.admin_view(_user_data_import),
                name="user_data_import",
            ),
            path(
                "user-data/apply-fixtures/",
                admin.site.admin_view(_user_data_apply_fixtures),
                name="user_data_apply_fixtures",
            ),
            path(
                "user-data/reset-fixtures/",
                admin.site.admin_view(_user_data_reset_fixtures),
                name="user_data_reset_fixtures",
            ),
            path(
                "user-data/toggle/<str:app_label>/<str:model_name>/<str:object_id>/",
                admin.site.admin_view(toggle_user_datum),
                name="user_data_toggle",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
