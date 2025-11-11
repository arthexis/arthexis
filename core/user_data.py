from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import json

from django.apps import apps
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.functional import LazyObject
from django.utils.translation import gettext as _
from django.utils.http import url_has_allowed_host_and_scheme

from .entity import Entity


def _data_root(user=None) -> Path:
    path = Path(getattr(user, "data_path", "") or Path(settings.BASE_DIR) / "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _username_for(user) -> str:
    username = ""
    if hasattr(user, "get_username"):
        username = user.get_username()
    if not username and hasattr(user, "username"):
        username = user.username
    if not username and getattr(user, "pk", None):
        username = str(user.pk)
    return username


def _user_allows_user_data(user) -> bool:
    if not user:
        return False
    username = _username_for(user)
    UserModel = get_user_model()
    system_username = getattr(UserModel, "SYSTEM_USERNAME", "")
    if system_username and username == system_username:
        return True
    return not getattr(user, "is_profile_restricted", False)


def _data_dir(user) -> Path:
    username = _username_for(user)
    if not username:
        raise ValueError("Cannot determine username for fixture directory")
    path = _data_root(user) / username
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fixture_path(user, instance) -> Path:
    model_meta = instance._meta.concrete_model._meta
    filename = f"{model_meta.app_label}_{model_meta.model_name}_{instance.pk}.json"
    return _data_dir(user) / filename


_SEED_FIXTURE_IGNORED_FIELDS = {"is_seed_data", "is_deleted", "is_user_data"}


@lru_cache(maxsize=1)
def _seed_fixture_index():
    base = Path(settings.BASE_DIR)
    index: dict[str, dict[str, object]] = {}
    for path in base.glob("**/fixtures/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list) or not data:
            continue
        obj = data[0]
        if not isinstance(obj, dict):
            continue
        label = obj.get("model")
        if not isinstance(label, str):
            continue
        fields = obj.get("fields") or {}
        if not isinstance(fields, dict):
            fields = {}
        comparable_fields = {
            key: value
            for key, value in fields.items()
            if key not in _SEED_FIXTURE_IGNORED_FIELDS
        }
        pk = obj.get("pk")
        entries = index.setdefault(label, {"pk": {}, "fields": []})
        pk_index = entries.setdefault("pk", {})
        field_index = entries.setdefault("fields", [])
        if pk is not None:
            pk_index[pk] = path
        if comparable_fields:
            field_index.append((comparable_fields, path))
    return index


def _seed_fixture_path(instance, *, index=None) -> Path | None:
    label = f"{instance._meta.app_label}.{instance._meta.model_name}"
    fixture_index = index or _seed_fixture_index()
    entries = fixture_index.get(label)
    if not entries:
        return None
    pk = getattr(instance, "pk", None)
    pk_index = entries.get("pk", {})
    if pk is not None:
        path = pk_index.get(pk)
        if path is not None:
            return path
    for comparable_fields, path in entries.get("fields", []):
        match = True
        if not isinstance(comparable_fields, dict):
            continue
        for field_name, value in comparable_fields.items():
            if not hasattr(instance, field_name):
                match = False
                break
            if getattr(instance, field_name) != value:
                match = False
                break
        if match:
            return path
    return None


def _coerce_user(candidate, user_model):
    if candidate is None:
        return None
    if isinstance(candidate, user_model):
        return candidate
    if isinstance(candidate, LazyObject):
        try:
            candidate._setup()
        except Exception:
            return None
        return _coerce_user(candidate._wrapped, user_model)
    return None


def _select_fixture_user(candidate, user_model):
    user = _coerce_user(candidate, user_model)
    visited: set[int] = set()
    while user is not None:
        identifier = user.pk or id(user)
        if identifier in visited:
            break
        visited.add(identifier)
        username = _username_for(user)
        admin_username = getattr(user_model, "ADMIN_USERNAME", "")
        if admin_username and username == admin_username:
            try:
                delegate = getattr(user, "operate_as", None)
            except user_model.DoesNotExist:
                delegate = None
            else:
                delegate = _coerce_user(delegate, user_model)
            if delegate is not None and delegate is not user:
                user = delegate
                continue
        if _user_allows_user_data(user):
            return user
        try:
            delegate = getattr(user, "operate_as", None)
        except user_model.DoesNotExist:
            delegate = None
        user = _coerce_user(delegate, user_model)
    return None


def _resolve_fixture_user(instance, fallback=None):
    UserModel = get_user_model()
    owner = getattr(instance, "user", None)
    selected = _select_fixture_user(owner, UserModel)
    if selected is not None:
        return selected
    if hasattr(instance, "owner"):
        try:
            owner_value = instance.owner
        except Exception:
            owner_value = None
        else:
            selected = _select_fixture_user(owner_value, UserModel)
            if selected is not None:
                return selected
    selected = _select_fixture_user(fallback, UserModel)
    if selected is not None:
        return selected
    return fallback


def dump_user_fixture(instance, user=None) -> None:
    model = instance._meta.concrete_model
    UserModel = get_user_model()
    if issubclass(UserModel, Entity) and isinstance(instance, UserModel):
        return
    target_user = user or _resolve_fixture_user(instance)
    if target_user is None:
        return
    allow_user_data = _user_allows_user_data(target_user)
    if not allow_user_data:
        is_user_data = getattr(instance, "is_user_data", False)
        if not is_user_data and instance.pk:
            stored_flag = (
                type(instance)
                .all_objects.filter(pk=instance.pk)
                .values_list("is_user_data", flat=True)
                .first()
            )
            is_user_data = bool(stored_flag)
        if not is_user_data:
            return
    meta = model._meta
    path = _fixture_path(target_user, instance)
    natural = getattr(model, "natural_key", None)
    if callable(natural):
        deps = getattr(natural, "dependencies", None)
        if isinstance(deps, (list, tuple, set)):
            normalized: list[str] = []
            updated = False
            for dep in deps:
                if isinstance(dep, str):
                    normalized.append(dep)
                elif hasattr(dep, "_meta") and getattr(dep._meta, "label_lower", None):
                    normalized.append(dep._meta.label_lower)
                    updated = True
                else:
                    normalized.append(dep)
            if updated:
                natural.dependencies = normalized
    call_command(
        "dumpdata",
        f"{meta.app_label}.{meta.model_name}",
        indent=2,
        pks=str(instance.pk),
        output=str(path),
        use_natural_foreign_keys=True,
    )


def delete_user_fixture(instance, user=None) -> None:
    target_user = user or _resolve_fixture_user(instance)
    meta = instance._meta.concrete_model._meta
    filename = f"{meta.app_label}_{meta.model_name}_{instance.pk}.json"

    def _remove_for_user(candidate) -> None:
        if candidate is None:
            return
        base_path = Path(
            getattr(candidate, "data_path", "") or Path(settings.BASE_DIR) / "data"
        )
        username = _username_for(candidate)
        if not username:
            return
        user_dir = base_path / username
        if user_dir.exists():
            (user_dir / filename).unlink(missing_ok=True)

    if target_user is not None:
        _remove_for_user(target_user)
        return

    root = Path(settings.BASE_DIR) / "data"
    if root.exists():
        (root / filename).unlink(missing_ok=True)
        for path in root.iterdir():
            if path.is_dir():
                (path / filename).unlink(missing_ok=True)

    UserModel = get_user_model()
    manager = getattr(UserModel, "all_objects", UserModel._default_manager)
    for candidate in manager.all():
        data_path = getattr(candidate, "data_path", "")
        if not data_path:
            continue
        base_path = Path(data_path)
        if not base_path.exists():
            continue
        username = _username_for(candidate)
        if not username:
            continue
        user_dir = base_path / username
        if user_dir.exists():
            (user_dir / filename).unlink(missing_ok=True)


def _mark_fixture_user_data(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = path.read_bytes().decode("latin-1")
        except Exception:
            return
    except Exception:
        return
    try:
        data = json.loads(content)
    except Exception:
        return
    if not isinstance(data, list):
        return
    for obj in data:
        label = obj.get("model")
        if not label:
            continue
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        if not issubclass(model, Entity):
            continue
        pk = obj.get("pk")
        if pk is None:
            continue
        model.all_objects.filter(pk=pk).update(is_user_data=True)


def _fixture_targets_installed_apps(data) -> bool:
    """Return ``True`` when *data* only targets installed apps and models."""

    if not isinstance(data, list):
        return True

    labels = {
        obj.get("model")
        for obj in data
        if isinstance(obj, dict) and obj.get("model")
    }

    for label in labels:
        if not isinstance(label, str):
            continue
        if "." not in label:
            continue
        app_label, model_name = label.split(".", 1)
        if not app_label or not model_name:
            continue
        if not apps.is_installed(app_label):
            return False
        try:
            apps.get_model(label)
        except LookupError:
            return False

    return True


def _load_fixture(path: Path, *, mark_user_data: bool = True) -> bool:
    """Load a fixture from *path* and optionally flag loaded entities."""

    text = None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_bytes().decode("latin-1")
        except Exception:
            return False
        path.write_text(text, encoding="utf-8")
    except Exception:
        # Continue without cached text so ``call_command`` can surface the
        # underlying error just as before.
        pass

    if text is not None:
        try:
            data = json.loads(text)
        except Exception:
            data = None
        else:
            if isinstance(data, list):
                if not data:
                    path.unlink(missing_ok=True)
                    return False
                if not _fixture_targets_installed_apps(data):
                    return False

    try:
        call_command("loaddata", str(path), ignorenonexistent=True)
    except Exception:
        return False

    if mark_user_data:
        _mark_fixture_user_data(path)

    return True


def _fixture_sort_key(path: Path) -> tuple[int, str]:
    parts = path.name.split("_", 2)
    model_part = parts[1].lower() if len(parts) >= 2 else ""
    is_user = model_part == "user"
    return (0 if is_user else 1, path.name)


def _is_user_fixture(path: Path) -> bool:
    parts = path.name.split("_", 2)
    return len(parts) >= 2 and parts[1].lower() == "user"


def _get_request_ip(request) -> str:
    """Return the best-effort client IP for ``request``."""

    if request is None:
        return ""

    meta = getattr(request, "META", None)
    if not getattr(meta, "get", None):
        return ""

    forwarded = meta.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        for value in str(forwarded).split(","):
            candidate = value.strip()
            if candidate:
                return candidate

    remote = meta.get("REMOTE_ADDR")
    if remote:
        return str(remote).strip()

    return ""


_shared_fixtures_loaded = False


def load_shared_user_fixtures(*, force: bool = False, user=None) -> None:
    global _shared_fixtures_loaded
    if _shared_fixtures_loaded and not force:
        return
    root = _data_root(user)
    paths = sorted(root.glob("*.json"), key=_fixture_sort_key)
    for path in paths:
        if _is_user_fixture(path):
            continue
        _load_fixture(path)
    _shared_fixtures_loaded = True


def load_user_fixtures(user, *, include_shared: bool = False) -> None:
    if include_shared:
        load_shared_user_fixtures(user=user)
    paths = sorted(_data_dir(user).glob("*.json"), key=_fixture_sort_key)
    for path in paths:
        if _is_user_fixture(path):
            continue
        _load_fixture(path)


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    load_user_fixtures(user, include_shared=not _shared_fixtures_loaded)

    if not (
        getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
    ):
        return

    # Login Net Messages were previously sent for staff authentication events.
    # They have been retired in favor of less noisy auditing, so no additional
    # side effects occur here beyond loading fixtures.


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def _on_user_created(sender, instance, created, **kwargs):
    if created:
        load_shared_user_fixtures(force=True, user=instance)
        load_user_fixtures(instance)


class UserDatumAdminMixin(admin.ModelAdmin):
    """Mixin adding a *User Datum* checkbox to change forms."""

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        supports_user_datum = issubclass(self.model, Entity) or getattr(
            self.model, "supports_user_datum", False
        )
        supports_seed_datum = issubclass(self.model, Entity) or getattr(
            self.model, "supports_seed_datum", supports_user_datum
        )
        context["show_user_datum"] = supports_user_datum
        context["show_seed_datum"] = supports_seed_datum
        context["show_save_as_copy"] = (
            issubclass(self.model, Entity)
            or getattr(self.model, "supports_save_as_copy", False)
            or hasattr(self.model, "clone")
        )
        if obj is not None:
            context["is_user_datum"] = getattr(obj, "is_user_data", False)
            context["is_seed_datum"] = getattr(obj, "is_seed_data", False)
        else:
            context["is_user_datum"] = False
            context["is_seed_datum"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)


class EntityModelAdmin(UserDatumAdminMixin, admin.ModelAdmin):
    """ModelAdmin base class for :class:`Entity` models."""

    change_form_template = "admin/user_datum_change_form.html"

    def save_model(self, request, obj, form, change):
        copied = "_saveacopy" in request.POST
        if copied:
            obj = obj.clone() if hasattr(obj, "clone") else obj
            obj.pk = None
            form.instance = obj
            try:
                super().save_model(request, obj, form, False)
            except Exception:
                messages.error(
                    request,
                    _("Unable to save copy. Adjust unique fields and try again."),
                )
                raise
        else:
            super().save_model(request, obj, form, change)
            if isinstance(obj, Entity):
                type(obj).all_objects.filter(pk=obj.pk).update(
                    is_seed_data=obj.is_seed_data, is_user_data=obj.is_user_data
                )
        if copied:
            return
        if getattr(self, "_skip_entity_user_datum", False):
            return

        target_user = _resolve_fixture_user(obj, request.user)
        allow_user_data = _user_allows_user_data(target_user)
        if request.POST.get("_user_datum") == "on":
            if allow_user_data:
                if not obj.is_user_data:
                    type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=True)
                    obj.is_user_data = True
                dump_user_fixture(obj, target_user)
                handler = getattr(self, "user_datum_saved", None)
                if callable(handler):
                    handler(request, obj)
                path = _fixture_path(target_user, obj)
                self.message_user(request, f"User datum saved to {path}")
            else:
                if obj.is_user_data:
                    type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=False)
                    obj.is_user_data = False
                    delete_user_fixture(obj, target_user)
                messages.warning(
                    request,
                    _("User data is not available for this account."),
                )
        elif obj.is_user_data:
            type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=False)
            obj.is_user_data = False
            delete_user_fixture(obj, target_user)
            handler = getattr(self, "user_datum_deleted", None)
            if callable(handler):
                handler(request, obj)


def patch_admin_user_datum() -> None:
    """Mixin all registered entity admin classes and future registrations."""

    if getattr(admin.site, "_user_datum_patched", False):
        return

    def _patched(admin_class):
        template = (
            getattr(admin_class, "change_form_template", None)
            or EntityModelAdmin.change_form_template
        )
        return type(
            f"Patched{admin_class.__name__}",
            (EntityModelAdmin, admin_class),
            {"change_form_template": template},
        )

    for model, model_admin in list(admin.site._registry.items()):
        if issubclass(model, Entity) and not isinstance(model_admin, EntityModelAdmin):
            admin.site.unregister(model)
            admin.site.register(model, _patched(model_admin.__class__))

    original_register = admin.site.register

    def register(model_or_iterable, admin_class=None, **options):
        models = model_or_iterable
        if not isinstance(models, (list, tuple, set)):
            models = [models]
        admin_class = admin_class or admin.ModelAdmin
        patched_class = admin_class
        for model in models:
            if issubclass(model, Entity) and not issubclass(
                patched_class, EntityModelAdmin
            ):
                patched_class = _patched(patched_class)
        return original_register(model_or_iterable, patched_class, **options)

    admin.site.register = register
    admin.site._user_datum_patched = True


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


def _safe_next_url(request):
    candidate = request.POST.get("next") or request.GET.get("next")
    if not candidate:
        return None

    allowed_hosts = {request.get_host()}
    allowed_hosts.update(filter(None, settings.ALLOWED_HOSTS))

    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts=allowed_hosts,
        require_https=request.is_secure(),
    ):
        return candidate
    return None


def _supports_user_datum(model) -> bool:
    return issubclass(model, Entity) or getattr(model, "supports_user_datum", False)


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
            items.append({"url": url, "label": str(obj), "fixture": fixture})
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
    context = admin.site.each_context(request)
    context.update(
        {"title": _("User Data"), "sections": sections, "import_export": True}
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


def patch_admin_user_data_views() -> None:
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "seed-data/", admin.site.admin_view(_seed_data_view), name="seed_data"
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
                "user-data/toggle/<str:app_label>/<str:model_name>/<str:object_id>/",
                admin.site.admin_view(toggle_user_datum),
                name="user_data_toggle",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
