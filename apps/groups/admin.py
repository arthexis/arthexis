from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.admin import GROUP_PROFILE_INLINES
from apps.core.admin.mixins import OwnedObjectLinksMixin
from apps.core.models import get_owned_objects_for_group
from .models import SecurityGroup


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
        if self.instance.pk:
            self.fields["users"].initial = self.instance.user_set.all()

    def save(self, commit=True):
        instance = super().save(commit)
        users = self.cleaned_data.get("users")
        if commit:
            instance.user_set.set(users)
        else:
            self.save_m2m = lambda: instance.user_set.set(users)
        return instance


class SecurityGroupAdmin(OwnedObjectLinksMixin, DjangoGroupAdmin):
    form = SecurityGroupAdminForm
    change_form_template = "admin/groups/securitygroup/change_form.html"
    fieldsets = (
        (None, {"fields": ("name", "parent", "site_template", "users", "permissions")}),
    )
    filter_horizontal = ("permissions",)
    search_fields = ("name", "parent__name")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.pk == request.user.groups.first():  # type: ignore[comparison-overlap]
            messages.warning(
                request,
                _(
                    "You are editing the first group assigned to your account. Changing it may affect your permissions."
                ),
            )
        return super().get_readonly_fields(request, obj)

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if obj is not None:
            change_password_url = reverse("admin:auth_user_password_change", args=[request.user.pk])
            fieldsets.append(
                (
                    _("Current user"),
                    {
                        "fields": (),
                        "description": _(
                            "Logged in as %(username)s. <a href='%(url)s'>Change password</a>"
                            % {"username": request.user.get_username(), "url": change_password_url}
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


admin.site.register(SecurityGroup, SecurityGroupAdmin)
SecurityGroupAdmin.inlines = GROUP_PROFILE_INLINES
