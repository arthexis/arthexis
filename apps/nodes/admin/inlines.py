from django.contrib import admin

from apps.credentials.models import SSHAccount

from ..models import NodeFeatureAssignment


class NodeFeatureAssignmentInline(admin.TabularInline):
    model = NodeFeatureAssignment
    extra = 0
    autocomplete_fields = ("feature",)


class SSHAccountInline(admin.StackedInline):
    model = SSHAccount
    extra = 0
    fields = (
        "username",
        "password",
        "private_key",
        "public_key",
    )
