from django.contrib import admin

from apps.credentials.forms import SSHAccountAdminForm
from apps.credentials.models import SSHAccount

from ..models import NodeFeatureAssignment


class NodeFeatureAssignmentInline(admin.TabularInline):
    model = NodeFeatureAssignment
    extra = 0
    autocomplete_fields = ("feature",)


class SSHAccountInline(admin.StackedInline):
    model = SSHAccount
    form = SSHAccountAdminForm
    extra = 0
    fields = (
        "username",
        "password",
        "private_key_media",
        "private_key_upload",
        "public_key_media",
        "public_key_upload",
    )
