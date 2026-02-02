from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.functional import LazyObject
from django.utils.translation import gettext as _, ngettext

from apps.core.entity import Entity
from .transfer import ImportExportAdminMixin


logger = logging.getLogger(__name__)



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



def _fixture_entry_targets_installed_apps(obj) -> bool:
    """Return ``True`` when *obj* targets an installed app and model."""

    if not isinstance(obj, dict):
        return True

    label = obj.get("model")
    if not isinstance(label, str):
        return True
    if "." not in label:
        return False

    app_label, model_name = label.split(".", 1)
    if not app_label or not model_name:
        return False
    if app_label not in apps.app_configs and not apps.is_installed(app_label):
        return False
    try:
        apps.get_model(label)
    except LookupError:
        return False

    return True



def _fixture_entry_targets_user_data_model(obj) -> bool:
    """Return ``True`` when *obj* targets a model that supports user data."""

    if not isinstance(obj, dict):
        return False

    label = obj.get("model")
    if not isinstance(label, str):
        return False
    try:
        model = apps.get_model(label)
    except LookupError:
        return False

    return issubclass(model, Entity) or getattr(model, "supports_user_datum", False)



def _filter_fixture_entries(data: object) -> tuple[object, bool]:
    """Return filtered fixture data and whether anything was removed."""

    if not isinstance(data, list):
        return data, False

    filtered = [
        obj
        for obj in data
        if _fixture_entry_targets_installed_apps(obj)
        and _fixture_entry_targets_user_data_model(obj)
    ]
    return filtered, len(filtered) != len(data)



def _load_fixture(
    path: Path, *, mark_user_data: bool = True, verbosity: int = 0
) -> bool:
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

    temp_path = None
    try:
        if text is not None:
            try:
                data = json.loads(text)
            except Exception:
                data = None
            else:
                filtered, filtered_out = _filter_fixture_entries(data)
                if isinstance(filtered, list):
                    if not filtered:
                        if not data:
                            path.unlink(missing_ok=True)
                        return False
                    if filtered_out:
                        temp_file = tempfile.NamedTemporaryFile(
                            mode="w",
                            suffix=path.suffix,
                            delete=False,
                        )
                        json.dump(filtered, temp_file)
                        temp_file.close()
                        temp_path = Path(temp_file.name)

        try:
            verbosity_level = max(0, int(verbosity))
        except (TypeError, ValueError):
            verbosity_level = 0

        call_command(
            "load_user_data",
            str(temp_path or path),
            ignorenonexistent=True,
            verbosity=verbosity_level,
        )
    except Exception:
        return False
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

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
    loaded = 0
    for path in paths:
        if _is_user_fixture(path):
            continue
        if _load_fixture(path):
            loaded += 1
    if loaded:
        logger.info("Loaded %d shared user data fixture(s)", loaded)
    _shared_fixtures_loaded = True



def load_user_fixtures(user, *, include_shared: bool = False) -> None:
    if include_shared:
        load_shared_user_fixtures(user=user)
    paths = sorted(_data_dir(user).glob("*.json"), key=_fixture_sort_key)
    loaded = 0
    for path in paths:
        if _is_user_fixture(path):
            continue
        if _load_fixture(path):
            loaded += 1
    if loaded:
        username = _username_for(user) or "unknown user"
        logger.info("Loaded %d user data fixture(s) for %s", loaded, username)


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

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not _supports_user_datum(self.model):
            return actions

        action = self.get_action("toggle_selected_user_data")
        if action is not None:
            actions.setdefault("toggle_selected_user_data", action)
        return actions

    @admin.action(description=_("Toggle selected User Data"))
    def toggle_selected_user_data(self, request, queryset):
        if not _supports_user_datum(self.model):
            messages.warning(
                request,
                _("User data is not available for this model."),
            )
            return

        manager = getattr(self.model, "all_objects", self.model._default_manager)
        toggled = 0
        skipped = 0

        for obj in queryset:
            target_user = _resolve_fixture_user(obj, request.user)
            allow_user_data = _user_allows_user_data(target_user)
            if getattr(obj, "is_user_data", False):
                manager.filter(pk=obj.pk).update(is_user_data=False)
                obj.is_user_data = False
                delete_user_fixture(obj, target_user)
                handler = getattr(self, "user_datum_deleted", None)
                if callable(handler):
                    handler(request, obj)
                toggled += 1
                continue

            if not allow_user_data:
                skipped += 1
                continue

            manager.filter(pk=obj.pk).update(is_user_data=True)
            obj.is_user_data = True
            dump_user_fixture(obj, target_user)
            handler = getattr(self, "user_datum_saved", None)
            if callable(handler):
                handler(request, obj)
            toggled += 1

        if toggled:
            opts = self.model._meta
            self.message_user(
                request,
                ngettext(
                    "Toggled user data for %(count)d %(verbose_name)s.",
                    "Toggled user data for %(count)d %(verbose_name_plural)s.",
                    toggled,
                )
                % {
                    "count": toggled,
                    "verbose_name": opts.verbose_name,
                    "verbose_name_plural": opts.verbose_name_plural,
                },
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d object because user data is not available.",
                    "Skipped %(count)d objects because user data is not available.",
                    skipped,
                )
                % {"count": skipped},
                level=messages.WARNING,
            )


class EntityModelAdmin(ImportExportAdminMixin, UserDatumAdminMixin, admin.ModelAdmin):
    """ModelAdmin base class for :class:`Entity` models."""

    change_form_template = "admin/user_datum_change_form.html"
    change_list_template = "admin/base/entity_change_list.html"
    soft_deleted_change_list_template = "admin/base/soft_deleted_change_list.html"
    soft_deleted_purge_template = "admin/base/soft_deleted_purge.html"

    def _supports_soft_delete(self) -> bool:
        return any(field.name == "is_deleted" for field in self.model._meta.fields)

    @admin.display(description="Owner")
    def owner_label(self, obj):
        return obj.owner_display()

    def _admin_view_name(self, suffix: str) -> str:
        opts = self.model._meta
        return f"{opts.app_label}_{opts.model_name}_{suffix}"

    def _soft_deleted_changelist_url(self):
        try:
            return reverse(f"admin:{self._admin_view_name('deleted_changelist')}")
        except NoReverseMatch:
            return None

    def _soft_deleted_purge_url(self):
        try:
            return reverse(f"admin:{self._admin_view_name('purge_deleted')}")
        except NoReverseMatch:
            return None

    def _active_changelist_url(self):
        try:
            return reverse(f"admin:{self._admin_view_name('changelist')}")
        except NoReverseMatch:
            return None

    def get_soft_deleted_queryset(self, request):
        manager = getattr(self.model, "all_objects", self.model._default_manager)
        return manager.filter(is_deleted=True)

    def get_queryset(self, request):
        if getattr(request, "_soft_deleted_only", False):
            return self.get_soft_deleted_queryset(request)
        return super().get_queryset(request)

    def get_urls(self):
        urls = super().get_urls()
        if not self._supports_soft_delete():
            return urls
        custom_urls = [
            path(
                "deleted/",
                self.admin_site.admin_view(self.soft_deleted_changelist_view),
                name=self._admin_view_name("deleted_changelist"),
            ),
            path(
                "deleted/purge/",
                self.admin_site.admin_view(self.purge_deleted_view),
                name=self._admin_view_name("purge_deleted"),
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        if self._supports_soft_delete():
            extra_context.setdefault("soft_delete_supported", True)
            extra_context.setdefault(
                "soft_deleted_url", self._soft_deleted_changelist_url()
            )
            extra_context.setdefault(
                "soft_deleted_purge_url", self._soft_deleted_purge_url()
            )
            extra_context.setdefault(
                "soft_deleted_active_url", self._active_changelist_url()
            )
            extra_context.setdefault(
                "soft_deleted_count",
                self.get_soft_deleted_queryset(request).count(),
            )
        if getattr(request, "_soft_deleted_only", False):
            extra_context.setdefault("soft_deleted_view", True)
        change_list_template = self.change_list_template
        if getattr(request, "_soft_deleted_only", False):
            self.change_list_template = self.soft_deleted_change_list_template
        try:
            return super().changelist_view(request, extra_context=extra_context)
        finally:
            self.change_list_template = change_list_template

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self._supports_soft_delete():
            return actions
        if getattr(request, "_soft_deleted_only", False):
            action = self.get_action("recover_selected")
            if action is not None:
                actions.setdefault("recover_selected", action)
        return actions

    @admin.action(description=_("Recover selected"))
    def recover_selected(self, request, queryset):
        if not self._supports_soft_delete():
            messages.warning(
                request,
                _("Recovery is not available for this model."),
            )
            return
        if not self.has_change_permission(request):
            raise PermissionDenied
        manager = getattr(self.model, "all_objects", self.model._default_manager)
        recovered = manager.filter(pk__in=queryset.values_list("pk", flat=True)).update(
            is_deleted=False
        )
        if recovered:
            self.message_user(
                request,
                ngettext(
                    "Recovered %(count)d deleted %(verbose_name)s.",
                    "Recovered %(count)d deleted %(verbose_name_plural)s.",
                    recovered,
                )
                % {
                    "count": recovered,
                    "verbose_name": self.model._meta.verbose_name,
                    "verbose_name_plural": self.model._meta.verbose_name_plural,
                },
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No deleted %(name)s were recovered.")
                % {"name": self.model._meta.verbose_name_plural},
                level=messages.WARNING,
            )

    def soft_deleted_changelist_view(self, request):
        if not self._supports_soft_delete():
            raise Http404
        request._soft_deleted_only = True
        return self.changelist_view(request)

    def _purge_soft_deleted_queryset(self, request):
        queryset = self.get_soft_deleted_queryset(request)
        purged = 0
        protected = []
        for obj in queryset:
            try:
                obj.delete()
            except ProtectedError:
                protected.append(obj)
                continue
            purged += 1
        return purged, protected

    def purge_deleted_view(self, request):
        if not self._supports_soft_delete():
            raise Http404
        if not self.has_delete_permission(request):
            raise PermissionDenied
        soft_deleted_count = self.get_soft_deleted_queryset(request).count()
        if request.method == "POST":
            purged, protected = self._purge_soft_deleted_queryset(request)
            if purged:
                self.message_user(
                    request,
                    ngettext(
                        "Purged %(count)d deleted %(name)s.",
                        "Purged %(count)d deleted %(name)s.",
                        purged,
                    )
                    % {"count": purged, "name": self.model._meta.verbose_name_plural},
                    level=messages.SUCCESS,
                )
            if protected:
                self.message_user(
                    request,
                    ngettext(
                        "Unable to purge %(count)d deleted %(name)s because related objects exist.",
                        "Unable to purge %(count)d deleted %(name)s because related objects exist.",
                        len(protected),
                    )
                    % {
                        "count": len(protected),
                        "name": self.model._meta.verbose_name_plural,
                    },
                    level=messages.ERROR,
                )
            return HttpResponseRedirect(self._soft_deleted_changelist_url() or "..")
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Confirm purge of deleted %(name)s")
            % {"name": self.model._meta.verbose_name_plural},
            "soft_deleted_count": soft_deleted_count,
            "soft_deleted_purge_url": self._soft_deleted_purge_url(),
            "soft_deleted_changelist_url": self._soft_deleted_changelist_url(),
            "soft_deleted_active_url": self._active_changelist_url(),
        }
        return TemplateResponse(request, self.soft_deleted_purge_template, context)

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

def _supports_user_datum(model) -> bool:
    return issubclass(model, Entity) or getattr(model, "supports_user_datum", False)



def _user_fixture_paths(user):
    return [
        path
        for path in sorted(_data_dir(user).glob("*.json"), key=_fixture_sort_key)
        if not _is_user_fixture(path)
    ]



def _read_fixture_entries(path: Path) -> list[dict]:
    try:
        content_bytes = path.read_bytes()
    except (OSError, IOError):
        return []

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = content_bytes.decode("latin-1")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    filtered, _ = _filter_fixture_entries(data)
    if not isinstance(filtered, list):
        return []
    return [obj for obj in filtered if isinstance(obj, dict)]



def _fixture_has_unapplied_entries(path: Path) -> bool:
    entries = _read_fixture_entries(path)
    pks_by_model: dict[type, list] = {}
    for obj in entries:
        label = obj.get("model")
        pk = obj.get("pk")
        if not label or pk is None:
            continue
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        pks_by_model.setdefault(model, []).append(pk)

    for model, pks in pks_by_model.items():
        try:
            unique_pks = set(pks)
        except TypeError:
            return True
        manager = getattr(model, "all_objects", model._default_manager)
        try:
            existing = manager.filter(pk__in=unique_pks).count()
        except (ValueError, TypeError):
            return True
        if existing < len(unique_pks):
            return True
    return False



def _user_fixture_status(user):
    paths = _user_fixture_paths(user)
    pending = [path for path in paths if _fixture_has_unapplied_entries(path)]
    return {"pending": pending, "total": paths}



def _apply_user_fixture_paths(request, paths, *, action_label: str, empty_message):
    if not paths:
        messages.warning(request, empty_message)
        return
    loaded = 0
    for path in paths:
        if _load_fixture(path):
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
