"""Public views for Evergo customer profiles and dashboard access."""

from __future__ import annotations

from urllib.parse import quote_plus

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from .exceptions import EvergoAPIError
from .models import EvergoArtifact, EvergoOrder, EvergoCustomer, EvergoUser


def customer_public_detail(request, pk: int) -> HttpResponse:
    """Render a public Evergo customer profile and artifacts."""
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
    pdf_artifact = next((artifact for artifact in artifacts if artifact.is_pdf), None)

    context = {
        "customer": customer,
        "google_maps_url": google_maps_url,
        "google_maps_embed_url": google_maps_embed_url,
        "image_artifacts": [artifact for artifact in artifacts if artifact.is_image],
        "pdf_artifact": pdf_artifact,
    }
    return render(request, "evergo/customer_public_detail.html", context)


def _build_full_address(order: EvergoOrder) -> str:
    """Build a copy/paste-ready full address from order address fragments."""
    line_one = " ".join(part for part in [order.address_street, order.address_num_ext] if part)
    if order.address_num_int:
        line_one = f"{line_one} Int {order.address_num_int}".strip()
    line_two = ", ".join(
        part
        for part in [
            order.address_neighborhood,
            order.address_city or order.address_municipality,
            order.address_state,
            order.address_postal_code,
        ]
        if part
    )
    combined = " - ".join(part for part in [line_one, line_two] if part)
    return combined or order.address_between_streets or ""


def my_dashboard(request, token: str) -> HttpResponse:
    """Render the public My Evergo Dashboard for a signed profile token."""
    profile = None
    rows: list[dict[str, str]] = []
    input_value = ""
    error_message = ""

    try:
        profile = EvergoUser.resolve_dashboard_token(token=token)
    except EvergoAPIError as exc:
        error_message = str(exc)
    else:
        if request.method == "POST":
            input_value = str(request.POST.get("sales_orders") or "")
            if input_value.strip():
                try:
                    summary = profile.load_customers_from_queries(raw_queries=input_value)
                except EvergoAPIError as exc:
                    error_message = str(exc)
                else:
                    order_numbers = summary["sales_orders"]
                    orders = profile.orders.filter(order_number__in=order_numbers).order_by("order_number")
                    rows = [
                        {
                            "so": order.order_number,
                            "customer_name": order.client_name,
                            "status": order.status_name,
                            "full_address": _build_full_address(order),
                            "phone": order.phone_primary or order.phone_secondary,
                            "charger_brand": order.site_name,
                            "city": order.address_municipality or order.address_city,
                        }
                        for order in orders
                    ]

    context = {
        "profile": profile,
        "username": profile.user.get_username() if profile and profile.user_id else "",
        "portal_orders_url": "https://portal-mex.evergo.com/ordenes/lista",
        "input_value": input_value,
        "rows": rows,
        "error_message": error_message,
    }
    return render(request, "evergo/my_dashboard.html", context)


def customer_artifact_download(request, pk: int, artifact_id: int) -> HttpResponse:
    """Download a PDF artifact attached to a customer profile."""
    artifact = get_object_or_404(EvergoArtifact, pk=artifact_id, customer_id=pk)
    if not artifact.is_pdf:
        raise Http404("Only PDF artifacts can be downloaded from this endpoint.")

    payload = artifact.file.read()
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{artifact.filename}"'
    return response
