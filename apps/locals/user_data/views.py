from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from django.apps import apps
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers import serialize
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.text import get_valid_filename
from django.utils.translation import gettext as _, ngettext

from .admin import _iter_entity_admin_models, _supports_seed_datum, _supports_user_datum
from .fixtures import (
    _data_dir,
    _load_fixture,
    _user_fixture_status,
    delete_user_fixture,
    dump_user_fixture,
    fixture_path,
    resolve_fixture_user,
    user_allows_user_data,
)
from .seeds import (
    _seed_datum_is_default,
    _seed_fixture_entries_from_text,
    _seed_fixture_index,
    _seed_fixture_name,
    _seed_fixture_path,
    _seed_fixture_text_from_bytes,
    _seed_zip_dir,
    load_local_seed_zips,
)
from .utils import _safe_next_url


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
    target_user = resolve_fixture_user(obj, request.user)
    allow_user_data = user_allows_user_data(target_user)
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
        path = fixture_path(target_user, obj)
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


def _seed_data_allowed_models() -> dict[str, tuple[type, admin.ModelAdmin]]:
    allowed: dict[str, tuple[type, admin.ModelAdmin]] = {}
    for model, model_admin in _iter_entity_admin_models():
        if not _supports_seed_datum(model):
            continue
        label = f"{model._meta.app_label}.{model._meta.model_name}"
        allowed[label] = (model, model_admin)
    return allowed


def _seed_fixture_entries_authorized(
    request, entries: list[dict]
) -> tuple[bool, str]:
    allowed = _seed_data_allowed_models()
    for obj in entries:
        if not isinstance(obj, dict):
            return False, _("Seed data fixture contains invalid entries.")
        label = obj.get("model")
        if not label or label not in allowed:
            return (
                False,
                _("Seed data fixture targets an unsupported model: %(model)s.")
                % {"model": label or _("unknown")},
            )
        if request.user.is_superuser:
            continue
        _, model_admin = allowed[label]
        if not (
            model_admin.has_add_permission(request)
            and model_admin.has_change_permission(request)
        ):
            return (
                False,
                _("You do not have permission to import seed data for %(model)s.")
                % {"model": label},
            )
    return True, ""


def _require_seed_data_permission(request) -> None:
    if not request.user.is_superuser:
        raise PermissionDenied


def _seed_data_view(request):
    _require_seed_data_permission(request)
    loaded = load_local_seed_zips()
    if loaded:
        messages.success(
            request,
            ngettext(
                "Applied %(count)d local seed data fixture.",
                "Applied %(count)d local seed data fixtures.",
                loaded,
            )
            % {"count": loaded},
        )
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
            fixture_name = (
                fixture.name if fixture is not None else _seed_fixture_name(model)
            )
            target_user = resolve_fixture_user(obj, request.user)
            allow_user_data = user_allows_user_data(target_user)
            custom = False
            if allow_user_data and target_user:
                custom = fixture_path(target_user, obj).exists()
            items.append(
                {
                    "url": url,
                    "label": str(obj),
                    "fixture_name": fixture_name,
                    "custom": custom,
                }
            )
        sections.append({"opts": model._meta, "items": items})
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Seed Data"),
            "sections": sections,
            "seed_data_actions": True,
            "seed_data_download_url": reverse("admin:seed_data_export"),
            "seed_data_upload_url": reverse("admin:seed_data_import"),
            "seed_data_upload_dir": str(Path("config") / "seeds"),
        }
    )
    return TemplateResponse(request, "admin/data_list.html", context)


def _seed_data_export(request):
    _require_seed_data_permission(request)
    buffer = BytesIO()
    fixture_index = _seed_fixture_index()
    from apps.core.fixtures import ensure_seed_data_flags

    with ZipFile(buffer, "w") as zf:
        for model, model_admin in _iter_entity_admin_models():
            objs = model.objects.filter(is_seed_data=True)
            if not objs.exists():
                continue
            local_ids = [
                obj.pk
                for obj in objs
                if not _seed_datum_is_default(obj, index=fixture_index)
            ]
            if not local_ids:
                continue
            queryset = model.objects.filter(pk__in=local_ids)
            payload = serialize(
                "json",
                queryset,
                use_natural_foreign_keys=True,
            )
            payload = ensure_seed_data_flags(payload)
            zf.writestr(_seed_fixture_name(model), payload)
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = "attachment; filename=local_seed_data.zip"
    return response


def _seed_data_import(request):
    _require_seed_data_permission(request)
    if request.method == "POST" and request.FILES.get("seed_zip"):
        seed_zip = request.FILES["seed_zip"]
        target_dir = _seed_zip_dir()
        try:
            with ZipFile(seed_zip) as zf:
                validated = False
                for name in zf.namelist():
                    if not name.endswith(".json"):
                        continue
                    content_bytes = zf.read(name)
                    text = _seed_fixture_text_from_bytes(content_bytes)
                    if text is None:
                        messages.error(
                            request, _("Seed data fixture includes unreadable content.")
                        )
                        return HttpResponseRedirect(reverse("admin:seed_data"))
                    entries = _seed_fixture_entries_from_text(text)
                    if not entries:
                        continue
                    authorized, message = _seed_fixture_entries_authorized(
                        request, entries
                    )
                    if not authorized:
                        messages.error(request, message)
                        return HttpResponseRedirect(reverse("admin:seed_data"))
                    validated = True
            seed_zip.seek(0)
        except Exception:
            messages.error(request, _("Invalid seed data ZIP file."))
            return HttpResponseRedirect(reverse("admin:seed_data"))
        if not validated:
            messages.warning(
                request, _("Seed data ZIP did not include any supported fixtures.")
            )
            return HttpResponseRedirect(reverse("admin:seed_data"))
        filename = get_valid_filename(Path(seed_zip.name).name)
        if not filename.lower().endswith(".zip"):
            filename = f"{filename}.zip"
        target_path = target_dir / filename
        with target_path.open("wb") as f:
            for chunk in seed_zip.chunks():
                f.write(chunk)
        loaded = load_local_seed_zips(only_paths=[target_path])
        if loaded:
            messages.success(
                request,
                ngettext(
                    "Applied %(count)d local seed data fixture.",
                    "Applied %(count)d local seed data fixtures.",
                    loaded,
                )
                % {"count": loaded},
            )
        else:
            messages.warning(
                request,
                _("No missing seed data fixtures were applied."),
            )
    return HttpResponseRedirect(reverse("admin:seed_data"))


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
            fixture = fixture_path(request.user, obj)
            items.append(
                {"url": url, "label": str(obj), "fixture_name": fixture.name}
            )
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
                if not name.endswith(".json"):
                    continue
                content = zf.read(name)
                target = data_dir / name
                with target.open("wb") as f:
                    f.write(content)
                paths.append(target)
        if paths:
            for path in paths:
                _load_fixture(path)
    return HttpResponseRedirect(reverse("admin:user_data"))


def _apply_user_fixture_paths(request, paths, *, action_label: str, empty_message):
    if not paths:
        messages.warning(request, empty_message)
        return
    loaded = 0
    for fixture_item in paths:
        if _load_fixture(fixture_item):
            loaded += 1
    if loaded:
        message = ngettext(
            "%(action)s %(count)d user data fixture.",
            "%(action)s %(count)d user data fixtures.",
            loaded,
        ) % {"action": action_label, "count": loaded}
        messages.success(request, message)
    else:
        messages.warning(request, _("No user data fixtures were applied."))

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
            path(
                "seed-data/", admin.site.admin_view(_seed_data_view), name="seed_data"
            ),
            path(
                "seed-data/export/",
                admin.site.admin_view(_seed_data_export),
                name="seed_data_export",
            ),
            path(
                "seed-data/import/",
                admin.site.admin_view(_seed_data_import),
                name="seed_data_import",
            ),
            path(
                "user-data/", admin.site.admin_view(_user_data_view), name="user_data"
            ),
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
