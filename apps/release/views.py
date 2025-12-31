from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _

from apps.release.models import Feature, FeatureArtifact, FeatureTestCase, Package


def _base_queryset():
    return Feature.objects.select_related("package").prefetch_related(
        Prefetch("artifacts", queryset=FeatureArtifact.objects.order_by("pk")),
        Prefetch("test_cases", queryset=FeatureTestCase.objects.order_by("pk")),
    )


def feature_index(request):
    package_filter = request.GET.get("package")
    version_filter = request.GET.get("version")
    show_due_only = request.GET.get("due") == "1"

    queryset = _base_queryset().filter(is_active=True)
    if package_filter:
        queryset = queryset.filter(package__name__iexact=package_filter)
    features = list(queryset.order_by("package__name", "expected_version", "name"))

    if version_filter:
        filtered_ids = [
            feature.pk for feature in features if feature.is_due_for_version(version_filter)
        ]
        if show_due_only:
            features = [feature for feature in features if feature.pk in filtered_ids]

    packages = Package.objects.order_by("name")
    context = {
        "features": features,
        "packages": packages,
        "package_filter": package_filter or "",
        "version_filter": version_filter or "",
        "show_due_only": show_due_only,
        "title": _("Feature index"),
    }
    return render(request, "release/feature_index.html", context)


@staff_member_required
def feature_admin_index(request):
    return feature_index(request)


def feature_detail(request, package: str, slug: str):
    feature = get_object_or_404(
        _base_queryset(), package__name__iexact=package, slug=slug
    )
    return render(
        request,
        "release/feature_detail.html",
        {
            "feature": feature,
            "title": feature.name,
        },
    )
