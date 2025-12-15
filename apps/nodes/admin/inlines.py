from django.contrib import admin

from ..models import NodeFeatureAssignment, SSHAccount


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
