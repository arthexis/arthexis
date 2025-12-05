from django import forms
from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin
from apps.nodes.forms import NodeRoleMultipleChoiceField
from apps.pages.models import Landing

from .models import Module


class LandingInline(admin.TabularInline):
    model = Landing
    extra = 0
    fields = ("path", "label", "enabled", "track_leads", "validation_status", "validated_url_at")
    readonly_fields = ("validation_status", "validated_url_at")
    show_change_link = True


class ModuleAdminForm(forms.ModelForm):
    roles = NodeRoleMultipleChoiceField()

    class Meta:
        model = Module
        fields = (
            "roles",
            "application",
            "path",
            "menu",
            "priority",
            "is_default",
            "favicon",
            "security_group",
            "security_mode",
        )

    class Media:
        css = {"all": ("nodes/css/node_role_multiselect.css",)}


@admin.register(Module)
class ModuleAdmin(EntityModelAdmin):
    form = ModuleAdminForm
    list_display = (
        "application",
        "roles_display",
        "path",
        "menu",
        "landings_count",
        "priority",
        "is_default",
        "security_group",
        "security_mode",
    )
    list_filter = ("roles", "application", "security_group", "security_mode")
    fields = ModuleAdminForm.Meta.fields
    inlines = [LandingInline]
    list_select_related = ("application", "security_group")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(landing_count=Count("landings", distinct=True)).prefetch_related("roles")

    @admin.display(description=_("Landings"), ordering="landing_count")
    def landings_count(self, obj):
        return obj.landing_count

    @admin.display(description=_("Roles"))
    def roles_display(self, obj):
        roles = [role.name for role in obj.roles.all()]
        return ", ".join(roles) if roles else _("All")
