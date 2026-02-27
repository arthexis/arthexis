"""Public views for Evergo customer profiles and dashboard access."""

from __future__ import annotations

from urllib.parse import quote_plus

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from .exceptions import EvergoAPIError
from .models import EvergoArtifact, EvergoCustomer, EvergoOrder, EvergoUser


def customer_public_detail(request, pk: int) -> HttpResponse:
    """Render a public Evergo customer profile and artifacts."""
    customer = get_object_or_404(
        EvergoCustomer.objects.select_related("latest_order").prefetch_related("artifacts"),
        pk=pk,
    )
    artifacts = list(customer.artifacts.all())
    address = customer.address.strip()
    google_maps_url = (
        f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}"
        if address
        else ""
    )
    google_maps_embed_url = (
        f"https://maps.google.com/maps?q={quote_plus(address)}&z=15&output=embed"
        if address
        else ""
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


def _order_external_url(order: EvergoOrder) -> str:
    """Return the public Evergo portal URL for one order."""
    return f"https://portal-mex.evergo.com/ordenes/lista?numero={quote_plus(order.order_number)}"


def _serialize_dashboard_row(order: EvergoOrder) -> dict[str, str]:
    """Normalize a local order into dashboard row columns."""
    return {
        "so": order.order_number,
        "so_external_url": _order_external_url(order),
        "customer_name": order.client_name,
        "status": order.status_name,
        "full_address": _build_full_address(order),
        "phone": order.phone_primary or order.phone_secondary,
        "charger_brand": order.site_name,
        "city": order.address_municipality or order.address_city,
    }


def _rows_to_tsv(rows: list[dict[str, str]]) -> str:
    """Convert dashboard rows into a tab-separated copy/paste block."""
    if not rows:
        return ""
    headers = [
        "SO",
        "Customer Name",
        "Status",
        "Full Address",
        "Phone",
        "Charger Brand",
        "City (Municipio)",
    ]
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    row["so"],
                    row["customer_name"],
                    row["status"],
                    row["full_address"],
                    row["phone"],
                    row["charger_brand"],
                    row["city"],
                ]
            )
        )
    return "\n".join(lines)


def _find_local_orders(
    profile: EvergoUser,
    *,
    sales_orders: list[str],
) -> tuple[list[EvergoOrder], list[str]]:
    """Return locally cached orders for SOs and list unresolved SO identifiers."""
    local_orders = list(
        profile.orders.filter(order_number__in=sales_orders).order_by("order_number", "remote_id")
    )
    found_so = {order.order_number for order in local_orders if order.order_number}
    unresolved = [so for so in sales_orders if so not in found_so]
    return local_orders, unresolved


def _latest_orders_by_customer_names(profile: EvergoUser, *, customer_names: list[str]) -> list[EvergoOrder]:
    """Return latest known orders associated with customer names."""
    if not customer_names:
        return []

    orders: list[EvergoOrder] = []
    for name in customer_names:
        customer = (
            profile.customers.select_related("latest_order")
            .filter(name__icontains=name)
            .order_by("-latest_order_updated_at", "-pk")
            .first()
        )
        if customer and customer.latest_order_id:
            orders.append(customer.latest_order)
    return orders


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
            sales_orders, customer_names = profile.parse_customer_queries(raw_queries=input_value)

            local_orders, unresolved_sales_orders = _find_local_orders(
                profile,
                sales_orders=sales_orders,
            )
            order_map: dict[str, EvergoOrder] = {
                order.order_number: order for order in local_orders if order.order_number
            }

            should_sync = bool(unresolved_sales_orders or customer_names)
            if should_sync:
                try:
                    profile.load_customers_from_queries(raw_queries=input_value)
                except EvergoAPIError as exc:
                    error_message = str(exc)
                else:
                    refreshed_orders = profile.orders.filter(order_number__in=unresolved_sales_orders).order_by(
                        "order_number",
                        "remote_id",
                    )
                    for order in refreshed_orders:
                        if order.order_number and order.order_number not in order_map:
                            order_map[order.order_number] = order

            ordered_rows = [_serialize_dashboard_row(order_map[so]) for so in sales_orders if so in order_map]

            seen_order_ids = {
                order_map[so].pk for so in sales_orders if so in order_map and order_map[so].pk
            }
            for order in _latest_orders_by_customer_names(profile, customer_names=customer_names):
                if order.pk in seen_order_ids:
                    continue
                ordered_rows.append(_serialize_dashboard_row(order))
                seen_order_ids.add(order.pk)

            rows = ordered_rows

    context = {
        "profile": profile,
        "username": profile.user.get_username() if profile and profile.user_id else "",
        "portal_orders_url": "https://portal-mex.evergo.com/ordenes/lista",
        "input_value": input_value,
        "rows": rows,
        "rows_tsv": _rows_to_tsv(rows),
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
