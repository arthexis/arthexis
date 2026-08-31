from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import connections, router, transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.admin.mixins import OwnedObjectLinksMixin
from apps.core.models import get_owned_objects_for_group

from .constants import SITE_OPERATOR_GROUP_NAME
from .models import SecurityGroup


def _load_group_profile_inlines():
    try:
        from apps.core.admin import GROUP_PROFILE_INLINES
    except Exception:
        return []

    return GROUP_PROFILE_INLINES


class SecurityGroupAdminForm(forms.ModelForm):
    users = forms.ModelMultipleChoiceField(
        queryset=get_user_model().objects.all(),
        required=False,
        widget=admin.widgets.FilteredSelectMultiple("users", False),
    )

    class Meta:
        model = SecurityGroup
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and "users" in self.fields:
            self.fields["users"].initial = self.instance.user_set.all()

    def save(self, commit=True):
        instance = super().save(commit)
        if "users" not in self.cleaned_data:
            return instance
        users = self.cleaned_data["users"]
        if commit:
            instance.user_set.set(users)
        else:
            self.save_m2m = lambda: instance.user_set.set(users)
        return instance


class SecurityGroupAdmin(OwnedObjectLinksMixin, DjangoGroupAdmin):
    form = SecurityGroupAdminForm
    change_form_template = "admin/groups/securitygroup/change_form.html"
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "security_model_label",
                    "app",
                    "parent",
                    "site_template",
                    "users",
                    "permissions",
                )
            },
        ),
    )
    filter_horizontal = ("permissions",)
    list_display = ("name", "security_model_label", "app", "parent", "site_template")
    list_filter = ("app",)
    readonly_fields = ("security_model_label",)
    search_fields = ("name", "app", "parent__name")

    def has_add_permission(self, request):
        if not super().has_add_permission(request):
            return False
        if request.user.is_superuser:
            return True
        return not self._site_operator_group_ids_for_user(request.user)

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj=obj):
            return False
        if request.user.is_superuser or obj is None:
            return True
        return obj.pk not in self._site_operator_group_ids_for_user(request.user)

    def delete_queryset(self, request, queryset):
        if request.user.is_superuser:
            if self._has_missing_related_tables():
                return self._delete_security_groups_without_collector(queryset)
            return super().delete_queryset(request, queryset)
        site_operator_group_ids = self._site_operator_group_ids_for_user(request.user)
        allowed_queryset = queryset.exclude(pk__in=site_operator_group_ids)
        if self._has_missing_related_tables():
            return self._delete_security_groups_without_collector(allowed_queryset)
        return super().delete_queryset(request, allowed_queryset)

    def delete_model(self, request, obj):
        if self._has_missing_related_tables():
            return self._delete_security_groups_without_collector(
                self.model.objects.filter(pk=obj.pk)
            )
        return super().delete_model(request, obj)

    def get_deleted_objects(self, objs, request):
        if self._has_missing_related_tables():
            objects = list(objs)
            return (
                [str(obj) for obj in objects],
                {str(self.opts.verbose_name_plural): len(objects)},
                set(),
                [],
            )
        return super().get_deleted_objects(objs, request)

    def response_action(self, request, queryset):
        if request.POST.get("action") == "delete_selected" and not request.user.is_superuser:
            selected_group_ids = {
                int(pk)
                for pk in request.POST.getlist(ACTION_CHECKBOX_NAME)
                if str(pk).isdigit()
            }
            site_operator_group_ids = self._site_operator_group_ids_for_user(request.user)
            if selected_group_ids & site_operator_group_ids:
                raise PermissionDenied
        return super().response_action(request, queryset)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if "security_model_label" not in readonly_fields:
            readonly_fields.append("security_model_label")
        if (
            obj is not None
            and not request.user.is_superuser
            and obj.pk in self._site_operator_group_ids_for_user(request.user)
        ):
            for field_name in ("name", "users"):
                if field_name not in readonly_fields:
                    readonly_fields.append(field_name)
        if obj is not None and obj.pk == request.user.groups.first():  # type: ignore[comparison-overlap]
            messages.warning(
                request,
                _(
                    "You are editing the first group assigned to your account. Changing it may affect your permissions."
                ),
            )
        return readonly_fields

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if obj is not None:
            change_password_url = reverse(
                "admin:auth_user_password_change", args=[request.user.pk]
            )
            fieldsets.append(
                (
                    _("Current user"),
                    {
                        "fields": (),
                        "description": _(
                            "Logged in as {username}. <a href='{url}'>Change password</a>"
                        ).format(
                            username=request.user.get_username(),
                            url=change_password_url,
                        ),
                    },
                )
            )
        return fieldsets

    def render_change_form(
        self, request, context, add=False, change=False, form_url="", obj=None
    ):
        payload = None
        if obj is not None:
            direct, via = get_owned_objects_for_group(obj)
            payload = self._build_owned_object_context(
                direct, via, _("Owned by member users")
            )
        self._attach_owned_objects(context, payload)
        return super().render_change_form(
            request, context, add=add, change=change, form_url=form_url, obj=obj
        )

    def _site_operator_group_ids_for_user(self, user):
        return set(
            user.groups.filter(name=SITE_OPERATOR_GROUP_NAME).values_list(
                "pk", flat=True
            )
        )

    def _has_missing_related_tables(self):
        using = router.db_for_write(self.model)
        connection = connections[using]
        existing_tables = set(connection.introspection.table_names())
        for relation in self.model._meta.get_fields(include_hidden=True):
            related_model = getattr(relation, "related_model", None)
            if not related_model or relation.concrete or not relation.auto_created:
                continue
            if related_model._meta.db_table not in existing_tables:
                return True
        return False

    def _delete_security_groups_without_collector(self, queryset):
        using = router.db_for_write(self.model)
        group_ids = list(queryset.values_list("pk", flat=True))
        if not group_ids:
            return None
        user_groups = get_user_model().groups.through
        group_permissions = Group.permissions.through
        with transaction.atomic(using=using):
            self.model.objects.using(using).filter(parent_id__in=group_ids).update(
                parent=None
            )
            user_groups.objects.using(using).filter(group_id__in=group_ids)._raw_delete(
                using
            )
            group_permissions.objects.using(using).filter(
                group_id__in=group_ids
            )._raw_delete(using)
            self.model.objects.using(using).filter(pk__in=group_ids)._raw_delete(using)
            Group.objects.using(using).filter(pk__in=group_ids)._raw_delete(using)
        return None


admin.site.register(SecurityGroup, SecurityGroupAdmin)
SecurityGroupAdmin.inlines = _load_group_profile_inlines()
