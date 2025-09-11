from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django import forms
from django.urls import path, reverse
from django.shortcuts import redirect, get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.template.response import TemplateResponse

from .models import SecurityGroup

PERM_CHOICES = [
    ("view", _("View")),
    ("add", _("Add")),
    ("change", _("Change")),
    ("delete", _("Delete")),
]


class ModelPermissionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        users = kwargs.pop("users")
        groups = kwargs.pop("groups")
        super().__init__(*args, **kwargs)
        for code, label in PERM_CHOICES:
            self.fields[f"user_{code}"] = forms.ModelMultipleChoiceField(
                label=_("Users with %(perm)s permission") % {"perm": label},
                queryset=users,
                required=False,
            )
            self.fields[f"group_{code}"] = forms.ModelMultipleChoiceField(
                label=_("Groups with %(perm)s permission") % {"perm": label},
                queryset=groups,
                required=False,
            )


def model_permissions_view(request, app_label, model_name):
    content_type = get_object_or_404(
        ContentType, app_label=app_label, model=model_name
    )
    Model = content_type.model_class()
    perms = {
        code: Permission.objects.get(
            content_type=content_type, codename=f"{code}_{model_name}"
        )
        for code, _ in PERM_CHOICES
    }
    User = get_user_model()
    form_kwargs = {
        "users": User.objects.filter(is_superuser=False),
        "groups": SecurityGroup.objects.all(),
    }
    if request.method == "POST":
        form = ModelPermissionForm(request.POST, **form_kwargs)
        if form.is_valid():
            for code, perm in perms.items():
                perm.user_set.set(form.cleaned_data[f"user_{code}"])
                perm.group_set.set(form.cleaned_data[f"group_{code}"])
            return redirect(reverse("admin:app_list", args=[app_label]))
    else:
        initial = {}
        for code, perm in perms.items():
            initial[f"user_{code}"] = perm.user_set.all()
            initial[f"group_{code}"] = perm.group_set.all()
        form = ModelPermissionForm(initial=initial, **form_kwargs)
    context = admin.site.each_context(request)
    context.update(
        {
            "form": form,
            "opts": Model._meta,
            "app_label": app_label,
            "model_name": model_name,
            "title": _("Permissions for %(name)s")
            % {"name": Model._meta.verbose_name},
            "perm_choices": PERM_CHOICES,
        }
    )
    return TemplateResponse(request, "admin/model_permissions.html", context)


def patch_admin_model_permissions_view():
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "<str:app_label>/<str:model_name>/permissions/",
                admin.site.admin_view(model_permissions_view),
                name="model_permissions",
            )
        ]
        return custom + urls

    admin.site.get_urls = get_urls
