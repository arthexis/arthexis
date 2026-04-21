from inspect import signature
from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import flatten_fieldsets
from django.core.exceptions import FieldError, ValidationError
from django.db.models import Q
from django.http import HttpResponseBase, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

TEST_CREDENTIALS_LABEL = _("Test credentials")


def _build_credentials_actions(action_name, handler_name, description=TEST_CREDENTIALS_LABEL):
    def bulk_action(self, request, queryset):
        handler = getattr(self, handler_name)
        for obj in queryset:
            handler(request, obj)

    bulk_action.__name__ = action_name
    bulk_action = admin.action(description=description)(bulk_action)
    bulk_action.__name__ = action_name

    def change_action(self, request, obj):
        getattr(self, handler_name)(request, obj)

    change_action.__name__ = f"{action_name}_action"
    change_action.label = description
    change_action.short_description = description
    return bulk_action, change_action


class OwnableAdminForm(forms.ModelForm):
    """Enforce configurable ownership requirements for admin model forms."""

    owner_exactly_one: bool = False
    owner_field_names: tuple[str, ...] = ("user", "group")
    owner_required: bool = True

    owner_conflict_message = _("Select either a user or a security group, not both.")
    owner_required_message = _("Ownable objects must be assigned to a user or a security group.")
    owner_exactly_one_message = _("Select exactly one owner.")

    def _configured_owner_field_names(self) -> tuple[str, ...]:
        configured_names = getattr(self, "owner_field_names", ())
        owner_names: list[str] = []
        for field_name in configured_names:
            if field_name in self.fields:
                owner_names.append(field_name)
        return tuple(owner_names)

    def _resolved_owner_required(self) -> bool:
        return getattr(self._meta.model, "owner_required", self.owner_required)

    def _owner_validation_message(self, message, owner_field_names):
        return {field_name: message for field_name in owner_field_names}

    def normalize_owner_data(self, cleaned):
        return cleaned

    def clean(self):
        cleaned = self.normalize_owner_data(super().clean())
        owner_field_names = self._configured_owner_field_names()
        owners = [cleaned.get(field_name) for field_name in owner_field_names]
        owner_count = sum(owner is not None for owner in owners)

        if self.owner_exactly_one and owner_count != 1:
            raise ValidationError(
                self._owner_validation_message(
                    self.owner_exactly_one_message,
                    owner_field_names,
                )
            )
        if not self.owner_exactly_one and owner_count > 1:
            raise ValidationError(
                self._owner_validation_message(
                    self.owner_conflict_message,
                    owner_field_names,
                )
            )
        if self._resolved_owner_required() and owner_count == 0:
            raise ValidationError(
                self._owner_validation_message(
                    self.owner_required_message,
                    owner_field_names,
                )
            )
        return cleaned


class OwnableAdminMixin:
    """Normalize owner fieldsets and validation for ownable models."""

    ownable_fieldset = ("Owner", {"fields": ("user", "group")})
    ownable_form_class = OwnableAdminForm

    def _form_includes_ownable_validation(self, form_class):
        return form_class and issubclass(form_class, OwnableAdminForm)


    def get_form(self, request, obj=None, **kwargs):
        """Return a form class that enforces ownership validation and tolerates dynamic fields."""

        form_class = kwargs.get("form") or getattr(self, "form", None)
        if not self._form_includes_ownable_validation(form_class):
            if form_class:
                form_class = type(
                    f"Ownable{form_class.__name__}",
                    (OwnableAdminForm, form_class),
                    {},
                )
                kwargs["form"] = form_class
            else:
                kwargs["form"] = self.ownable_form_class
                form_class = kwargs["form"]

        model_field_names = {field.name for field in self.model._meta.get_fields()}
        declared_field_names = set(getattr(form_class, "declared_fields", {}).keys())
        allowed_fields = model_field_names | declared_field_names

        field_names = kwargs.get("fields")
        if field_names:
            kwargs["fields"] = [name for name in field_names if name in allowed_fields]

        try:
            return super().get_form(request, obj, **kwargs)
        except FieldError:
            kwargs.pop("fields", None)
            return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        owner_fields = set(self.ownable_fieldset[1].get("fields", ()))
        has_owner = any(owner_fields.issubset(set(fs[1].get("fields", ()))) for fs in fieldsets)
        if not has_owner:
            fieldsets.insert(0, self.ownable_fieldset)
        return fieldsets


class OwnedObjectLinksMixin:
    """Inject owned object summaries into change form context."""

    owned_object_context_key = "owned_object_links"

    def _build_owned_object_context(self, direct, via, via_label):
        if not direct and not via:
            return None
        return {
            "direct": direct,
            "via": via,
            "via_label": via_label,
        }

    def _attach_owned_objects(self, extra_context, payload):
        if payload:
            extra_context.setdefault(self.owned_object_context_key, payload)


class PublicViewLinksAdminMixin:
    """Expose public view shortcuts in admin object tools for changelists and objects."""

    public_view_links_context_key = "public_view_links"

    def get_public_view_links(self, obj=None, request=None) -> list[dict[str, str]]:
        """Return additional public links to render in admin object tools."""

        del request
        return []

    def _normalize_public_view_links(self, obj=None, request=None) -> list[dict[str, str]]:
        """Return de-duplicated public view links with stable labels."""

        normalized: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        parameters = signature(self.get_public_view_links).parameters
        kwargs = {}
        if "obj" in parameters:
            kwargs["obj"] = obj
        if "request" in parameters:
            kwargs["request"] = request
        for entry in self.get_public_view_links(**kwargs):
            url = (entry or {}).get("url")
            if not url:
                continue
            label = (entry.get("label") or _("View on site")).strip()
            key = (label, url)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"label": label, "url": url})
        return normalized

    def changelist_view(self, request, extra_context=None):
        """Inject public route shortcuts into changelist object tools."""

        extra_context = extra_context or {}
        links = self._normalize_public_view_links(request=request)
        if links:
            extra_context.setdefault(self.public_view_links_context_key, links)
        return super().changelist_view(request, extra_context=extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """Inject object-specific public route shortcuts into change-form tools."""

        extra_context = extra_context or {}
        obj = None
        if object_id:
            obj = self.get_object(request, object_id)
        links = self._normalize_public_view_links(obj=obj, request=request)
        if links:
            extra_context.setdefault(self.public_view_links_context_key, links)
        return super().changeform_view(
            request,
            object_id=object_id,
            form_url=form_url,
            extra_context=extra_context,
        )


class SaveBeforeChangeAction(DjangoObjectActions):
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(
            {
                "objectactions": [
                    self._get_tool_dict(action)
                    for action in self.get_change_actions(request, object_id, form_url)
                ],
                "tools_view_name": self.tools_view_name,
            }
        )
        return super().changeform_view(request, object_id, form_url, extra_context)

    def response_change(self, request, obj):
        action = request.POST.get("_action")
        if action:
            allowed = self.get_change_actions(request, str(obj.pk), None)
            if action in allowed and hasattr(self, action):
                response = getattr(self, action)(request, obj)
                if isinstance(response, HttpResponseBase):
                    return response
                return redirect(request.path)
        return super().response_change(request, obj)


class ProfileAdminMixin:
    """Reusable actions for profile-bound admin classes."""

    def _get_user_profile_info(self, request):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return user, None, 0

        group_ids = list(user.groups.values_list("id", flat=True))
        owner_filter = Q(user=user)
        if group_ids:
            owner_filter |= Q(group_id__in=group_ids)
        if hasattr(self.model, "avatar"):
            owner_filter |= Q(avatar__user=user)
            if group_ids:
                owner_filter |= Q(avatar__group_id__in=group_ids)

        queryset = self.model._default_manager.filter(owner_filter)
        profiles = list(queryset[:2])
        if not profiles:
            return user, None, 0
        if len(profiles) == 1:
            return user, profiles[0], 1
        return user, profiles[0], 2

    def get_my_profile_label(self, request):
        _user, profile, profile_count = self._get_user_profile_info(request)
        if profile_count == 0:
            return _("Active Profile (Unset)")
        if profile_count == 1 and profile is not None:
            return _("Active Profile (%(name)s)") % {"name": str(profile)}
        return _("Active Profile")

    def _resolve_my_profile_target(self, request):
        opts = self.model._meta
        changelist_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_changelist"
        )
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return (
                changelist_url,
                _("You must be logged in to manage your profile."),
                messages.ERROR,
            )

        _user, profile, profile_count = self._get_user_profile_info(request)
        if profile is not None:
            permission_check = getattr(self, "has_view_or_change_permission", None)
            has_permission = (
                permission_check(request, obj=profile)
                if callable(permission_check)
                else self.has_change_permission(request, obj=profile)
            )
            if has_permission:
                change_url = reverse(
                    f"admin:{opts.app_label}_{opts.model_name}_change",
                    args=[profile.pk],
                )
                return change_url, None, None
            return (
                changelist_url,
                _("You do not have permission to view this profile."),
                messages.ERROR,
            )

        if profile_count == 0 and self.has_add_permission(request):
            add_url = reverse(f"admin:{opts.app_label}_{opts.model_name}_add")
            params = {}
            user_id = getattr(user, "pk", None)
            if user_id:
                params["user"] = user_id
            if params:
                add_url = f"{add_url}?{urlencode(params)}"
            return add_url, None, None

        return (
            changelist_url,
            _("You do not have permission to create this profile."),
            messages.ERROR,
        )

    def get_my_profile_url(self, request):
        url, _message, _level = self._resolve_my_profile_target(request)
        return url

    def _redirect_to_my_profile(self, request):
        target_url, message, level = self._resolve_my_profile_target(request)
        if message:
            self.message_user(request, message, level=level)
        return HttpResponseRedirect(target_url)

    @admin.action(description=_("Active Profile"))
    def my_profile(self, request, queryset=None):
        return self._redirect_to_my_profile(request)

    def my_profile_action(self, request, obj=None):
        return self._redirect_to_my_profile(request)

    my_profile_action.label = _("Active Profile")
    my_profile_action.short_description = _("Active Profile")
