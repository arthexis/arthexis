from django.contrib import admin
from django.db.models import Count

from apps.locals.user_data import EntityModelAdmin

from ..models import NodeRole, Node
from .forms import NodeRoleAdminForm


@admin.register(NodeRole)
class NodeRoleAdmin(EntityModelAdmin):
    form = NodeRoleAdminForm
    list_display = ("name", "acronym", "description", "registered", "default_features")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_registered=Count("node", distinct=True)).prefetch_related(
            "features"
        )

    @admin.display(description="Registered", ordering="_registered")
    def registered(self, obj):
        return getattr(obj, "_registered", obj.node_set.count())

    @admin.display(description="Default Features")
    def default_features(self, obj):
        features = [feature.display for feature in obj.features.all()]
        return ", ".join(features) if features else "—"

    def save_model(self, request, obj, form, change):
        obj.node_set.set(form.cleaned_data.get("nodes", []))
