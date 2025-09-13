from __future__ import annotations

from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import json

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.signals import user_logged_in
from django.core.management import call_command
from django.dispatch import receiver
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext as _

from .entity import Entity


def _data_dir(user) -> Path:
    path = Path(getattr(user, "data_path") or Path(settings.BASE_DIR) / "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fixture_path(user, instance) -> Path:
    ct = instance._meta
    filename = f"{ct.app_label}_{ct.model_name}_{instance.pk}.json"
    return _data_dir(user) / filename


def _seed_fixture_path(instance) -> Path | None:
    label = f"{instance._meta.app_label}.{instance._meta.model_name}"
    base = Path(settings.BASE_DIR)
    for path in base.glob("**/fixtures/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list) or not data:
            continue
        obj = data[0]
        if obj.get("model") == label and obj.get("pk") == instance.pk:
            return path
    return None


def dump_user_fixture(instance, user) -> None:
    path = _fixture_path(user, instance)
    call_command(
        "dumpdata",
        f"{instance._meta.app_label}.{instance._meta.model_name}",
        indent=2,
        pks=str(instance.pk),
        output=str(path),
    )


def delete_user_fixture(instance, user) -> None:
    _fixture_path(user, instance).unlink(missing_ok=True)


def load_user_fixtures(user) -> None:
    paths = sorted(_data_dir(user).glob("*.json"))
    for path in paths:
        try:
            call_command("loaddata", str(path), ignorenonexistent=True)
        except UnicodeDecodeError:
            try:
                data = path.read_bytes().decode("latin-1")
            except Exception:
                continue
            path.write_text(data, encoding="utf-8")
            call_command("loaddata", str(path), ignorenonexistent=True)
        except Exception:
            continue


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    load_user_fixtures(user)


class UserDatumAdminMixin(admin.ModelAdmin):
    """Mixin adding a *User Datum* checkbox to change forms."""

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        context["show_user_datum"] = issubclass(self.model, Entity)
        context["show_seed_datum"] = issubclass(self.model, Entity)
        context["show_save_as_copy"] = issubclass(self.model, Entity) or hasattr(
            self.model, "clone"
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
        if request.POST.get("_user_datum") == "on":
            if not obj.is_user_data:
                type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=True)
                obj.is_user_data = True
            dump_user_fixture(obj, request.user)
            path = _fixture_path(request.user, obj)
            self.message_user(request, f"User datum saved to {path}")
        elif obj.is_user_data:
            type(obj).all_objects.filter(pk=obj.pk).update(is_user_data=False)
            obj.is_user_data = False
            delete_user_fixture(obj, request.user)


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


def _seed_data_view(request):
    sections = []
    for model, model_admin in admin.site._registry.items():
        if not issubclass(model, Entity):
            continue
        objs = model.objects.filter(is_seed_data=True)
        if not objs.exists():
            continue
        items = []
        for obj in objs:
            url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
            fixture = _seed_fixture_path(obj)
            items.append({"url": url, "label": str(obj), "fixture": fixture})
        sections.append({"opts": model._meta, "items": items})
    context = admin.site.each_context(request)
    context.update({"title": _("Seed Data"), "sections": sections})
    return TemplateResponse(request, "admin/data_list.html", context)


def _user_data_view(request):
    sections = []
    for model, model_admin in admin.site._registry.items():
        if not issubclass(model, Entity):
            continue
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
            call_command("loaddata", *[str(p) for p in paths], ignorenonexistent=True)
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
        ]
        return custom + urls

    admin.site.get_urls = get_urls
