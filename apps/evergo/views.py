"""Public views for Evergo customer profiles."""

from __future__ import annotations

from urllib.parse import quote_plus

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from .exceptions import EvergoAPIError, EvergoPhaseSubmissionError
from .forms import EvergoDashboardLookupForm, EvergoOrderTrackingForm
from .models import EvergoArtifact, EvergoCustomer, EvergoOrder, EvergoUser
from .services import ensure_image_payload


EVERGO_PORTAL_ORDER_URL_TEMPLATE = getattr(
    settings,
    "EVERGO_PORTAL_ORDER_URL_TEMPLATE",
    "https://portal-mex.evergo.com/ordenes/{order_id}",
)
EVERGO_PORTAL_MAIN_URL = getattr(settings, "EVERGO_PORTAL_MAIN_URL", "https://portal-mex.evergo.com/")


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


def customer_artifact_download(request, pk: int, artifact_id: int) -> HttpResponse:
    """Download a PDF artifact attached to a customer profile."""
    artifact = get_object_or_404(EvergoArtifact, pk=artifact_id, customer_id=pk)
    if not artifact.is_pdf:
        raise Http404("Only PDF artifacts can be downloaded from this endpoint.")

    payload = artifact.file.read()
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{artifact.filename}"'
    return response


def my_evergo_dashboard(request, token) -> HttpResponse:
    """Render the secure public dashboard for one Evergo profile token."""
    profile = get_object_or_404(EvergoUser.objects.select_related("user"), dashboard_token=token)
    rows: list[dict[str, str]] = []
    copy_table = ""
    form = EvergoDashboardLookupForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        raw_queries = form.cleaned_data["raw_queries"]
        sales_orders, customer_names = profile.parse_customer_queries(raw_queries=raw_queries)
        if sales_orders or customer_names:
            has_local = _has_any_local_matches(profile=profile, sales_orders=sales_orders, customer_names=customer_names)
            if not has_local:
                try:
                    profile.load_customers_from_queries(raw_queries=raw_queries)
                except EvergoAPIError as exc:
                    messages.warning(request, _("Evergo API sync failed: %(error)s") % {"error": str(exc)})
            rows = _build_dashboard_rows(profile=profile, sales_orders=sales_orders, customer_names=customer_names)
            copy_table = _to_tsv(rows)

    context = {
        "profile": profile,
        "form": form,
        "rows": rows,
        "copy_table": copy_table,
        "evergo_main_url": EVERGO_PORTAL_MAIN_URL,
    }
    return render(request, "evergo/my_evergo_dashboard.html", context)


def _has_any_local_matches(*, profile: EvergoUser, sales_orders: list[str], customer_names: list[str]) -> bool:
    """Check whether local cache already contains at least one row for dashboard lookups."""
    query = Q(user=profile)
    filters = Q()
    if sales_orders:
        filters |= Q(order_number__in=sales_orders)
    if customer_names:
        for name in customer_names:
            filters |= Q(client_name__icontains=name)
    if not filters:
        return False
    return EvergoOrder.objects.filter(query & filters).exists()


def _build_dashboard_rows(*, profile: EvergoUser, sales_orders: list[str], customer_names: list[str]) -> list[dict[str, str]]:
    """Assemble dashboard table rows from local EvergoOrder cache."""
    query = Q(user=profile)
    filters = Q()
    if sales_orders:
        filters |= Q(order_number__in=sales_orders)
    if customer_names:
        for name in customer_names:
            filters |= Q(client_name__icontains=name)

    if not filters:
        return []

    orders = EvergoOrder.objects.filter(query & filters).order_by("order_number", "remote_id")
    rows: list[dict[str, str]] = []
    for order in orders:
        rows.append(
            {
                "so": order.order_number or str(order.remote_id),
                "so_url": EVERGO_PORTAL_ORDER_URL_TEMPLATE.format(order_id=order.remote_id),
                "customer_name": order.client_name or "-",
                "status": order.status_name or "-",
                "full_address": _format_full_address(order),
                "phone": order.phone_primary or order.phone_secondary or "-",
                "charger_brand": order.site_name or "-",
                "city": order.address_municipality or order.address_city or "-",
            }
        )
    return rows


def _format_full_address(order: EvergoOrder) -> str:
    """Return single-line readable full address from cached order address fields."""
    parts = [
        order.address_street,
        order.address_num_ext,
        order.address_num_int,
        order.address_between_streets,
        order.address_neighborhood,
        order.address_municipality,
        order.address_city,
        order.address_state,
        order.address_postal_code,
    ]
    full = " ".join(part.strip() for part in parts if part and part.strip())
    return full or "-"


def _to_tsv(rows: list[dict[str, str]]) -> str:
    """Convert dashboard rows into copy/paste TSV text."""
    headers = ["SO", "Customer Name", "Status", "Full Address", "Phone", "Charger Brand", "City (Municipio)"]
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


@login_required
def order_tracking_public(request, order_id: int) -> HttpResponse:
    """Render and submit the order tracking phase-one helper form for authorized owners only."""
    order = get_object_or_404(
        EvergoOrder.objects.select_related("user"),
        remote_id=order_id,
        user__user=request.user,
    )
    profile = order.user
    brands = profile.fetch_charger_brand_options()

    if request.method == "POST":
        form = EvergoOrderTrackingForm(request.POST, request.FILES, charger_brands=brands)
        missing_images = [name for name in IMAGE_FIELD_NAMES if not form.files.get(name)]
        if form.is_valid():
            if missing_images and request.POST.get("confirm_missing_images") != "1":
                form.add_error(None, "Confirma que deseas continuar con imágenes faltantes.")
            else:
                payload = _build_phase_one_payload(form.cleaned_data)
                files = ensure_image_payload({name: form.cleaned_data.get(name) for name in IMAGE_FIELD_NAMES})
                messages.info(request, "Inicio de envío: 0/4 pasos completados.")
                try:
                    result = profile.submit_tracking_phase_one(order_id=order_id, payload=payload, files=files)
                except EvergoPhaseSubmissionError as exc:
                    messages.warning(
                        request,
                        f"Proceso parcial: {exc.completed_steps}/4 pasos completados.",
                    )
                    form.add_error(None, str(exc))
                except EvergoAPIError as exc:
                    form.add_error(None, str(exc))
                else:
                    completed_steps = int(result.get("completed_steps") or 4)
                    messages.success(
                        request,
                        f"Orden enviada correctamente. {completed_steps}/4 pasos completados.",
                    )
                    return redirect("evergo:order-tracking-public", order_id=order_id)
    else:
        form = EvergoOrderTrackingForm(charger_brands=brands)
        missing_images = []

    return render(
        request,
        "evergo/order_tracking_public.html",
        {
            "order": order,
            "form": form,
            "missing_images": missing_images,
            "image_field_names": IMAGE_FIELD_NAMES,
            "image_fields": [form[name] for name in IMAGE_FIELD_NAMES],
            "collapsed_defaults": COLLAPSED_DEFAULT_FIELDS,
            "collapsed_fields": [form[name] for name in COLLAPSED_DEFAULT_FIELDS],
            "evergo_so_url": EVERGO_PORTAL_ORDER_URL_TEMPLATE.format(order_id=order.remote_id),
        },
    )


IMAGE_FIELD_NAMES = [
    "foto_tablero",
    "foto_medidor",
    "foto_tierra",
    "foto_ruta_cableado",
    "foto_ubicacion_cargador",
    "foto_general",
    "foto_voltaje_fase_fase",
    "foto_voltaje_fase_tierra",
    "foto_voltaje_fase_neutro",
    "foto_voltaje_neutro_tierra",
    "foto_hoja_visita",
    "foto_interruptor_principal",
    "foto_panoramica_estacion",
    "foto_numero_serie_cargador",
    "foto_interruptor_instalado",
    "foto_conexion_cargador",
    "foto_preparacion_cfe",
    "foto_hoja_reporte_instalacion",
]

COLLAPSED_DEFAULT_FIELDS = [
    "tipo_visita",
    "requiere_instalacion",
    "tipo_inmueble",
    "concentracion_medidores",
    "servicio",
    "obra_civil",
    "kit_cfe",
    "calibre_principal",
    "garantia",
]


def _build_phase_one_payload(cleaned_data: dict[str, object]) -> dict[str, object]:
    """Map form values to a transport payload consumed by Evergo integration calls."""
    payload = {k: v for k, v in cleaned_data.items() if k not in IMAGE_FIELD_NAMES}
    if "fecha_visita" in payload and payload["fecha_visita"] is not None:
        payload["fecha_visita"] = payload["fecha_visita"].strftime("%Y-%m-%d %H:%M:%S")
    amp = str(payload.get("programacion_cargador") or "")
    payload["programacion_cargador_visita"] = amp
    payload["programacion_cargador_instalacion"] = amp.replace("A", "")
    return payload
