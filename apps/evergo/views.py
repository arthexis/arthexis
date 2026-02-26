"""Public views for Evergo customer profiles."""

from __future__ import annotations

from urllib.parse import quote_plus

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import EvergoArtifact, EvergoCustomer
from .public_links import (
    build_artifact_signature,
    is_valid_artifact_signature,
    is_valid_customer_signature,
)


def customer_public_detail(request, pk: int) -> HttpResponse:
    """Render a public Evergo customer profile and artifacts."""
    if not is_valid_customer_signature(pk, request.GET.get("sig", "")):
        raise PermissionDenied("Missing or invalid signature.")

    customer = get_object_or_404(
        EvergoCustomer.objects.select_related("latest_order").prefetch_related("artifacts"),
        pk=pk,
    )
    artifacts = list(customer.artifacts.all())
    address = customer.address.strip()
    google_maps_url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}" if address else ""
    google_maps_embed_url = (
        f"https://maps.google.com/maps?q={quote_plus(address)}&z=15&output=embed" if address else ""
    )

    image_artifacts = []
    pdf_artifact = None
    for artifact in artifacts:
        if artifact.is_image:
            image_artifacts.append(artifact)
            continue
        if artifact.is_pdf and pdf_artifact is None:
            pdf_artifact = artifact

    context = {
        "customer": customer,
        "google_maps_url": google_maps_url,
        "google_maps_embed_url": google_maps_embed_url,
        "image_artifacts": image_artifacts,
        "pdf_artifact": pdf_artifact,
        "artifact_download_signature": (
            build_artifact_signature(customer.pk, pdf_artifact.pk) if pdf_artifact else ""
        ),
    }
    return render(request, "evergo/customer_public_detail.html", context)


def customer_artifact_download(request, pk: int, artifact_id: int) -> HttpResponse:
    """Download a PDF artifact attached to a customer profile."""
    if not is_valid_artifact_signature(pk, artifact_id, request.GET.get("sig", "")):
        raise PermissionDenied("Missing or invalid signature.")

    artifact = get_object_or_404(EvergoArtifact, pk=artifact_id, customer_id=pk)
    if not artifact.is_pdf:
        raise Http404("Only PDF artifacts can be downloaded from this endpoint.")

    return FileResponse(
        artifact.file.open("rb"),
        as_attachment=True,
        filename=artifact.filename,
        content_type="application/pdf",
    )
