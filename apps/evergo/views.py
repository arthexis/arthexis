"""Public views for Evergo customer profiles and dashboard access."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from .exceptions import EvergoAPIError
from .models import EvergoArtifact, EvergoCustomer, EvergoOrder, EvergoUser

PORTAL_ORDERS_URL = "https://portal-mex.evergo.com/ordenes/lista"
PORTAL_ORDER_URL_PATTERN = "https://portal-mex.evergo.com/ordenes/{remote_id}"
USERNAME_TOKEN_PATTERN = re.compile(r"@([\w.\-]{2,80})")


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


def _serialize_dashboard_row(order: EvergoOrder) -> dict[str, str]:
    """Normalize a local order into dashboard row columns."""
    remote_id = str(order.remote_id) if order.remote_id else ""
    return {
        "so": order.order_number,
        "so_url": PORTAL_ORDER_URL_PATTERN.format(remote_id=remote_id) if remote_id else PORTAL_ORDERS_URL,
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


def _find_local_orders(profile: EvergoUser, *, sales_orders: list[str]) -> tuple[list[EvergoOrder], list[str]]:
    """Return locally cached orders for SOs and list any unresolved SO identifiers."""
    local_orders = list(
        profile.orders.filter(order_number__in=sales_orders).order_by("order_number", "-source_updated_at", "remote_id")
    )
    found_so = {order.order_number for order in local_orders if order.order_number}
    unresolved = [so for so in sales_orders if so not in found_so]
    return local_orders, unresolved


def _extract_username_queries(raw_text: str) -> list[str]:
    """Extract normalized @username tokens from free-form input."""
    seen: set[str] = set()
    usernames: list[str] = []
    for username in USERNAME_TOKEN_PATTERN.findall(raw_text or ""):
        normalized = " ".join(str(username).strip().split())
        if len(normalized) < 2:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        usernames.append(normalized)
    return usernames


def _find_latest_orders_by_username(profile: EvergoUser, *, usernames: list[str]) -> tuple[list[EvergoOrder], list[str]]:
    """Find latest local order for each username-like token by client name matching."""
    latest_orders: list[EvergoOrder] = []
    unresolved: list[str] = []
    for username in usernames:
        order = (
            profile.orders.filter(client_name__icontains=username)
            .order_by("-source_updated_at", "-remote_id")
            .first()
        )
        if order is None:
            unresolved.append(username)
            continue
        latest_orders.append(order)
    return latest_orders, unresolved


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
            username_queries = _extract_username_queries(input_value)

            local_orders, unresolved_sales_orders = _find_local_orders(profile, sales_orders=sales_orders)
            username_orders, unresolved_usernames = _find_latest_orders_by_username(
                profile,
                usernames=username_queries,
            )

            order_map: dict[str, EvergoOrder] = {
                order.order_number: order
                for order in [*local_orders, *username_orders]
                if order.order_number
            }

            needs_remote_sync = bool(unresolved_sales_orders or unresolved_usernames)
            if needs_remote_sync:
                try:
                    remote_queries = unresolved_sales_orders + [f"{name}" for name in unresolved_usernames]
                    profile.load_customers_from_queries(raw_queries="\n".join(remote_queries))
                except EvergoAPIError as exc:
                    error_message = str(exc)

            if unresolved_sales_orders:
                refreshed_orders = profile.orders.filter(order_number__in=unresolved_sales_orders).order_by(
                    "order_number",
                    "-source_updated_at",
                    "remote_id",
                )
                for order in refreshed_orders:
                    if order.order_number:
                        order_map.setdefault(order.order_number, order)

            if unresolved_usernames:
                refreshed_username_orders, _still_missing = _find_latest_orders_by_username(
                    profile,
                    usernames=unresolved_usernames,
                )
                for order in refreshed_username_orders:
                    if order.order_number:
                        order_map.setdefault(order.order_number, order)

            if customer_names:
                for customer_name in customer_names:
                    order = (
                        profile.orders.filter(client_name__icontains=customer_name)
                        .order_by("-source_updated_at", "-remote_id")
                        .first()
                    )
                    if order and order.order_number:
                        order_map.setdefault(order.order_number, order)

            ordered_so = [so for so in sales_orders if so in order_map]
            remaining_orders = [
                order
                for so, order in order_map.items()
                if so not in ordered_so
            ]
            ordered_orders = [order_map[so] for so in ordered_so] + sorted(
                remaining_orders,
                key=lambda order: ((order.source_updated_at or order.created_at), order.remote_id),
                reverse=True,
            )
            rows = [_serialize_dashboard_row(order) for order in ordered_orders]

    context = {
        "profile": profile,
        "username": profile.user.get_username() if profile and profile.user_id else "",
        "portal_orders_url": PORTAL_ORDERS_URL,
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
