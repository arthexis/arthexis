from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.template.response import TemplateResponse
from django.urls import path, reverse
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


# ---- Signals -------------------------------------------------------------

@receiver(post_save)
def _entity_saved(sender, instance, **kwargs):
    if not isinstance(instance, Entity):
        return
    ct = ContentType.objects.get_for_model(instance)
    for ud in UserDatum.objects.filter(content_type=ct, object_id=instance.pk):
        dump_user_fixture(instance, ud.user)


@receiver(post_delete)
def _entity_deleted(sender, instance, **kwargs):
    if not isinstance(instance, Entity):
        return
    ct = ContentType.objects.get_for_model(instance)
    for ud in UserDatum.objects.filter(content_type=ct, object_id=instance.pk):
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
        if issubclass(self.model, Entity):
            context["show_user_datum"] = True
            if obj is not None:
                ct = ContentType.objects.get_for_model(obj)
                context["is_user_datum"] = UserDatum.objects.filter(
                    user=request.user, content_type=ct, object_id=obj.pk
                ).exists()
            else:
                context["is_user_datum"] = False
        return super().render_change_form(request, context, add, change, form_url, obj)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not issubclass(self.model, Entity):
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
    """Mixin all registered admin classes for :class:`Entity` models."""
    for model, model_admin in list(admin.site._registry.items()):
        if not issubclass(model, Entity):
            continue
        if isinstance(model_admin, UserDatumAdminMixin):
            continue
        admin.site.unregister(model)
        template = (
            getattr(model_admin, "change_form_template", None)
            or "admin/user_datum_change_form.html"
        )
        attrs = {"change_form_template": template}
        Patched = type(
            f"Patched{model_admin.__class__.__name__}",
            (UserDatumAdminMixin, model_admin.__class__),
            attrs,
        )
        admin.site.register(model, Patched)


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
            items.append({"url": url, "label": str(obj)})
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
        section.append({"url": url, "label": str(obj)})
    section_list = [{"opts": opts, "items": items} for opts, items in sections.items()]
    context = admin.site.each_context(request)
    context.update({"title": _("User Data"), "sections": section_list})
    return TemplateResponse(request, "admin/data_list.html", context)


def patch_admin_user_data_views() -> None:
    """Add custom admin views for seed and user data listings."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("seed-data/", admin.site.admin_view(_seed_data_view), name="seed_data"),
            path("user-data/", admin.site.admin_view(_user_data_view), name="user_data"),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
