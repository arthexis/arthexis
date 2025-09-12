from __future__ import annotations

from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import json
import tempfile

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.translation import gettext as _

from .entity import Entity


class UserDatum(models.Model):
    """Link an :class:`Entity` instance to a user for persistence."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    entity = GenericForeignKey("content_type", "object_id")
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "content_type", "object_id")
        verbose_name = "User Datum"
        verbose_name_plural = "User Data"


# ---- Fixture utilities ---------------------------------------------------


def _data_dir() -> Path:
    path = Path(settings.BASE_DIR) / "data"
    path.mkdir(exist_ok=True)
    return path


def _fixture_path(user, instance) -> Path:
    ct = ContentType.objects.get_for_model(instance)
    filename = f"{user.pk}_{ct.app_label}_{ct.model}_{instance.pk}.json"
    return _data_dir() / filename


_seed_fixture_cache: dict[tuple[str, int], Path] | None = None


def _seed_fixture_path(instance) -> Path | None:
    """Return the fixture file containing the given seed datum."""
    global _seed_fixture_cache
    if _seed_fixture_cache is None:
        _seed_fixture_cache = {}
        base = Path(settings.BASE_DIR)
        for path in base.rglob("fixtures/*.json"):
            try:
                with path.open() as f:
                    data = json.load(f)
            except Exception:
                continue
            for obj in data:
                model = obj.get("model")
                pk = obj.get("pk")
                if model and pk is not None:
                    _seed_fixture_cache[(model, pk)] = path
    key = (f"{instance._meta.app_label}.{instance._meta.model_name}", instance.pk)
    return _seed_fixture_cache.get(key)


def ensure_userdatum(path: Path) -> None:
    """Ensure a :class:`UserDatum` entry exists for the given fixture."""
    try:
        user_id, app_label, model, obj_id = path.stem.split("_", 3)
        ct = ContentType.objects.get_by_natural_key(app_label, model)
        UserDatum.objects.get_or_create(
            user_id=int(user_id), content_type=ct, object_id=int(obj_id)
        )
    except Exception:
        pass


def dump_user_fixture(instance, user) -> None:
    path = _fixture_path(user, instance)
    app_label = instance._meta.app_label
    model_name = instance._meta.model_name
    call_command(
        "dumpdata",
        f"{app_label}.{model_name}",
        indent=2,
        pks=str(instance.pk),
        output=str(path),
    )


def delete_user_fixture(instance, user) -> None:
    _fixture_path(user, instance).unlink(missing_ok=True)


def load_user_fixtures() -> None:
    """Load all user datum fixtures from the data directory."""
    data_dir = _data_dir()
    personal = sorted(data_dir.glob("*.json"))
    if not personal:
        return
    User = get_user_model()
    with tempfile.TemporaryDirectory() as tmpdir:
        patched: list[str] = []
        for p in personal:
            dest = Path(tmpdir, p.name)
            try:
                data = json.load(p.open())
            except Exception:
                # Skip files that cannot be parsed as JSON
                continue
            try:
                user_id = int(p.stem.split("_", 1)[0])
                User.objects.get_or_create(
                    pk=user_id, defaults={"username": f"user{user_id}"}
                )
            except Exception:
                pass
            for obj in data:
                fields = obj.get("fields", {})
                uid = fields.get("user")
                if (
                    isinstance(uid, int)
                    and not User.all_objects.filter(pk=uid).exists()
                ):
                    fields["user"] = 1
            dest.write_text(json.dumps(data))
            patched.append(str(dest))
        if patched:
            call_command("loaddata", *patched, ignorenonexistent=True)
    for p in personal:
        ensure_userdatum(p)


# ---- Signals -------------------------------------------------------------


@receiver(post_save)
def _entity_saved(sender, instance, **kwargs):
    if isinstance(instance, UserDatum):
        return
    try:
        ct = ContentType.objects.get_for_model(instance)
        qs = list(UserDatum.objects.filter(content_type=ct, object_id=instance.pk))
    except Exception:
        return
    for ud in qs:
        dump_user_fixture(instance, ud.user)


@receiver(post_delete)
def _entity_deleted(sender, instance, **kwargs):
    if isinstance(instance, UserDatum):
        return
    try:
        ct = ContentType.objects.get_for_model(instance)
        qs = list(UserDatum.objects.filter(content_type=ct, object_id=instance.pk))
    except Exception:
        return
    for ud in qs:
        delete_user_fixture(instance, ud.user)
        ud.delete()


@receiver(post_save, sender=UserDatum)
def _userdatum_saved(sender, instance, **kwargs):
    dump_user_fixture(instance.entity, instance.user)


@receiver(post_delete, sender=UserDatum)
def _userdatum_deleted(sender, instance, **kwargs):
    delete_user_fixture(instance.entity, instance.user)


# ---- Admin integration ---------------------------------------------------


class UserDatumAdminMixin(admin.ModelAdmin):
    """Mixin adding a *User Datum* checkbox to change forms."""

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        context["show_user_datum"] = issubclass(self.model, Entity)
        context["show_seed_datum"] = issubclass(self.model, Entity)
        context["show_save_as_copy"] = issubclass(self.model, Entity) or hasattr(
            self.model,
            "clone",
        )
        if obj is not None:
            ct = ContentType.objects.get_for_model(obj)
            context["is_user_datum"] = UserDatum.objects.filter(
                user=request.user, content_type=ct, object_id=obj.pk
            ).exists()
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
            except IntegrityError:
                self.message_user(
                    request,
                    _("Unable to save copy. Adjust unique fields and try again."),
                    messages.ERROR,
                )
                raise ValidationError("save_as_copy")
        else:
            super().save_model(request, obj, form, change)
            if isinstance(obj, Entity):
                type(obj).all_objects.filter(pk=obj.pk).update(
                    is_seed_data=obj.is_seed_data
                )
        if copied:
            return
        ct = ContentType.objects.get_for_model(obj)
        if request.POST.get("_user_datum") == "on":
            UserDatum.objects.get_or_create(
                user=request.user, content_type=ct, object_id=obj.pk
            )
            dump_user_fixture(obj, request.user)
            path = _fixture_path(request.user, obj)
            self.message_user(request, f"User datum saved to {path}")
        else:
            qs = UserDatum.objects.filter(
                user=request.user, content_type=ct, object_id=obj.pk
            )
            if qs.exists():
                qs.delete()
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
        if (
            model is UserDatum
            or isinstance(model_admin, EntityModelAdmin)
            or not issubclass(model, Entity)
        ):
            continue
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
            if model is UserDatum:
                continue
            if issubclass(model, Entity) and not issubclass(
                patched_class, EntityModelAdmin
            ):
                patched_class = _patched(patched_class)
        return original_register(model_or_iterable, patched_class, **options)

    admin.site.register = register
    admin.site._user_datum_patched = True


def _seed_data_view(request):
    """Display all entities marked as seed data."""
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
            fixture_path = _seed_fixture_path(obj)
            items.append(
                {
                    "url": url,
                    "label": str(obj),
                    "fixture": fixture_path.name if fixture_path else "",
                }
            )
        sections.append({"opts": model._meta, "items": items})
    context = admin.site.each_context(request)
    context.update({"title": _("Seed Data"), "sections": sections})
    return TemplateResponse(request, "admin/data_list.html", context)


def _user_data_view(request):
    """Display all user datum entities for the current user."""
    sections = {}
    qs = UserDatum.objects.filter(user=request.user).select_related("content_type")
    for ud in qs:
        model = ud.content_type.model_class()
        obj = ud.entity
        url = reverse(
            f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
            args=[obj.pk],
        )
        section = sections.setdefault(model._meta, [])
        fixture = _fixture_path(request.user, obj)
        section.append({"url": url, "label": str(obj), "fixture": fixture.name})
    section_list = [{"opts": opts, "items": items} for opts, items in sections.items()]
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("User Data"),
            "sections": section_list,
            "import_export": True,
        }
    )
    return TemplateResponse(request, "admin/data_list.html", context)


def _user_data_export(request):
    """Return a zip file containing all fixtures for the current user."""
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zf:
        for path in _data_dir().glob(f"{request.user.pk}_*.json"):
            zf.write(path, arcname=path.name)
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = (
        f"attachment; filename=user_data_{request.user.pk}.zip"
    )
    return response


def _user_data_import(request):
    """Import fixtures from an uploaded zip file."""
    if request.method == "POST" and request.FILES.get("data_zip"):
        with ZipFile(request.FILES["data_zip"]) as zf:
            paths = []
            data_dir = _data_dir()
            for name in zf.namelist():
                if not name.endswith(".json"):
                    continue
                content = zf.read(name)
                try:
                    data = json.loads(content)
                except Exception:
                    continue
                if not data:
                    continue
                target = data_dir / name
                with target.open("wb") as f:
                    f.write(content)
                paths.append(target)
        if paths:
            call_command("loaddata", *[str(p) for p in paths])
            for p in paths:
                ensure_userdatum(p)
    return HttpResponseRedirect(reverse("admin:user_data"))


def patch_admin_user_data_views() -> None:
    """Add custom admin views for seed and user data listings."""
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
