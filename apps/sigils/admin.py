from django import forms
from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from .models import CustomSigil, SigilRoot


class CustomSigilAdminForm(forms.ModelForm):
    class Meta:
        model = CustomSigil
        fields = ["prefix", "content_type"]


@admin.register(CustomSigil)
class CustomSigilAdmin(EntityModelAdmin):
    form = CustomSigilAdminForm
    list_display = ("prefix", "content_type")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(context_type=SigilRoot.Context.ENTITY)

    def save_model(self, request, obj, form, change):
        obj.context_type = SigilRoot.Context.ENTITY
        super().save_model(request, obj, form, change)


@admin.register(SigilRoot)
class SigilRootAdmin(EntityModelAdmin):
    list_display = (
        "prefix",
        "context_type",
        "content_type",
        "is_seed_data",
        "is_deleted",
    )
    list_filter = ("context_type", "is_seed_data", "is_deleted")
    search_fields = ("prefix",)
