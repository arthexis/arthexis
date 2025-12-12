from django.contrib import admin

from ..models import NodeFeatureAssignment


class NodeFeatureAssignmentInline(admin.TabularInline):
    model = NodeFeatureAssignment
    extra = 0
    autocomplete_fields = ("feature",)
