from django.contrib import admin

from apps.docs.models import (
    Cookbook,
    DocumentIndex,
    DocumentIndexAssignment,
    ModelDocumentation,
)


@admin.register(Cookbook)
class CookbookAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "file_name")
    search_fields = ("title", "slug", "file_name")


@admin.register(ModelDocumentation)
class ModelDocumentationAdmin(admin.ModelAdmin):
    filter_horizontal = ("models",)
    list_display = ("title", "doc_path")
    search_fields = ("title", "doc_path", "models__app_label", "models__model")


class DocumentIndexAssignmentInline(admin.TabularInline):
    autocomplete_fields = ("security_group",)
    extra = 1
    model = DocumentIndexAssignment


@admin.register(DocumentIndex)
class DocumentIndexAdmin(admin.ModelAdmin):
    inlines = (DocumentIndexAssignmentInline,)
    list_display = ("display_title", "doc_path", "listable")
    list_filter = ("listable", "assignments__access", "assignments__security_group")
    search_fields = ("title", "doc_path", "assignments__security_group__name")

    @admin.display(description="Title")
    def display_title(self, obj: DocumentIndex) -> str:
        return obj.title or obj.doc_path
