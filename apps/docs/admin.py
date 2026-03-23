from django.contrib import admin

from apps.docs.models import Cookbook, ModelDocumentation


@admin.register(Cookbook)
class CookbookAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "file_name")
    search_fields = ("title", "slug", "file_name")


@admin.register(ModelDocumentation)
class ModelDocumentationAdmin(admin.ModelAdmin):
    filter_horizontal = ("models",)
    list_display = ("title", "doc_path")
    search_fields = ("title", "doc_path", "models__app_label", "models__model")
