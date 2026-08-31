from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from .models import Feature


class FeatureDetailView(TemplateView):
    template_name = "features/feature_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        feature = get_object_or_404(Feature.objects, slug=self.kwargs.get("slug"))
        context["feature"] = feature
        context["notes"] = feature.notes.select_related("author").all()
        return context
