"""Public views for Evergo customer profiles."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
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

DISPLAY_TEXT_FIXUPS = {
    "Orden en ejecuci?n": "Orden en ejecución",
}

DATETIME_LOCAL_FORMAT = "%Y-%m-%dT%H:%M"

TRACKING_PREFILL_SOURCE_KEYS = (
    "reporte_visita",
    "reporte_visita_tecnica",
    "visita_tecnica",
    "reporte_instalacion",
    "instalacion",
    "seguimiento",
    "tracking",
    "data",
)


def _normalize_display_text(value: str | None, *, default: str = "-") -> str:
    """Normalize common encoding artifacts in user-facing Evergo text."""
    normalized = str(value or "").strip()
    if not normalized:
        return default
    return DISPLAY_TEXT_FIXUPS.get(normalized, normalized)


@login_required
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


@login_required
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
            has_complete_local = _has_all_local_matches(
                profile=profile,
                sales_orders=sales_orders,
                customer_names=customer_names,
            )
            if not has_complete_local:
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


def _build_dashboard_query_filters(*, sales_orders: list[str], customer_names: list[str]) -> Q:
    """Build shared Q filters for sales-order and customer-name dashboard lookups."""
    filters = Q()
    if sales_orders:
        filters |= Q(order_number__in=sales_orders)
    for name in customer_names:
        filters |= Q(client_name__icontains=name)
    return filters


def _has_all_local_matches(*, profile: EvergoUser, sales_orders: list[str], customer_names: list[str]) -> bool:
    """Return True only when local cache covers every requested lookup term."""
    if not sales_orders and not customer_names:
        return False

    query = Q(user=profile)
    orders_qs = EvergoOrder.objects.filter(query)

    if sales_orders:
        found_sales_orders = {
            number
            for number in orders_qs.filter(order_number__in=sales_orders).values_list("order_number", flat=True)
            if number
        }
        if any(so not in found_sales_orders for so in sales_orders):
            return False

    for name in customer_names:
        if not orders_qs.filter(client_name__icontains=name).exists():
            return False

    return True


def _build_dashboard_rows(*, profile: EvergoUser, sales_orders: list[str], customer_names: list[str]) -> list[dict[str, str]]:
    """Assemble dashboard table rows from local EvergoOrder cache."""
    query = Q(user=profile)
    filters = _build_dashboard_query_filters(sales_orders=sales_orders, customer_names=customer_names)
    if not filters:
        return []

    orders = EvergoOrder.objects.filter(query & filters).order_by("order_number", "remote_id")
    rows: list[dict[str, str]] = []
    for order in orders:
        rows.append(
            {
                "so": order.order_number or (str(order.remote_id) if order.remote_id is not None else "-"),
                "so_url": (
                    EVERGO_PORTAL_ORDER_URL_TEMPLATE.format(order_id=order.remote_id)
                    if order.remote_id is not None
                    else ""
                ),
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


def _sanitize_tsv_value(value: str | None) -> str:
    """Sanitize TSV cell values for structure and spreadsheet formula safety."""
    normalized = str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")
    if normalized.startswith(("=", "+", "-", "@")):
        return f"'" + normalized
    return normalized

def _to_tsv(rows: list[dict[str, str]]) -> str:
    """Convert dashboard rows into copy/paste TSV text with basic CSV-injection hardening."""
    headers = ["SO", "Customer Name", "Status", "Full Address", "Phone", "Charger Brand", "City (Municipio)"]
    lines = ["	".join(headers)]
    for row in rows:
        lines.append(
            "	".join(
                [
                    _sanitize_tsv_value(row["so"]),
                    _sanitize_tsv_value(row["customer_name"]),
                    _sanitize_tsv_value(row["status"]),
                    _sanitize_tsv_value(row["full_address"]),
                    _sanitize_tsv_value(row["phone"]),
                    _sanitize_tsv_value(row["charger_brand"]),
                    _sanitize_tsv_value(row["city"]),
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
    remote_image_urls: dict[str, str] = {}

    if request.method == "POST":
        form = EvergoOrderTrackingForm(request.POST, request.FILES, charger_brands=brands)
        remote_initial_data, _, remote_image_urls = _load_remote_phase_one_initial_data(
            profile=profile,
            order_id=order_id,
        )
        missing_images = [
            name
            for name in IMAGE_FIELD_NAMES
            if not form.files.get(name) and not remote_image_urls.get(name)
        ]
        if form.is_valid():
            if missing_images and request.POST.get("confirm_missing_images") != "1":
                form.add_error(None, "Confirma que deseas continuar con imágenes faltantes.")
            else:
                payload = _build_phase_one_payload(form.cleaned_data)
                files = ensure_image_payload({name: form.cleaned_data.get(name) for name in IMAGE_FIELD_NAMES})
                merged_step_values = dict(remote_initial_data)
                merged_step_values.update(form.cleaned_data)
                step_completion = _compute_tracking_step_completion(
                    _build_tracking_step_values(values=merged_step_values, remote_image_urls=remote_image_urls)
                )
                messages.info(request, "Inicio de envío: 0/4 pasos completados.")
                try:
                    result = profile.submit_tracking_phase_one(
                        order_id=order_id,
                        payload=payload,
                        files=files,
                        step_completion=step_completion,
                    )
                except EvergoPhaseSubmissionError as exc:
                    messages.warning(
                        request,
                        f"Proceso parcial: {exc.completed_steps}/4 pasos completados.",
                    )
                    form.add_error(None, str(exc))
                except EvergoAPIError as exc:
                    form.add_error(None, str(exc))
                else:
                    completed_steps_raw = result.get("completed_steps")
                    completed_steps = 4 if completed_steps_raw is None else int(completed_steps_raw)
                    messages.success(
                        request,
                        f"Orden enviada correctamente. {completed_steps}/4 pasos completados.",
                    )
                    return redirect("evergo:order-tracking-public", order_id=order_id)
    else:
        remote_initial_data, remote_prefill_errors, remote_image_urls = _load_remote_phase_one_initial_data(
            profile=profile,
            order_id=order_id,
        )
        form = EvergoOrderTrackingForm(charger_brands=brands, initial=remote_initial_data)
        for field_name, error_message in remote_prefill_errors.items():
            if field_name in form.fields:
                form.fields[field_name].help_text = error_message
        missing_images = []

    image_field_rows = _build_image_field_rows(form=form, remote_image_urls=remote_image_urls)
    step_status_values = _build_tracking_step_values(
        values=form.cleaned_data if form.is_bound else form.initial,
        remote_image_urls=remote_image_urls,
    )
    step_statuses = _build_tracking_step_statuses(step_status_values)

    return render(
        request,
        "evergo/order_tracking_public.html",
        {
            "order": order,
            "order_status_display": _normalize_display_text(order.status_name),
            "order_client_display": _normalize_display_text(order.client_name),
            "form": form,
            "missing_images": missing_images,
            "image_field_names": IMAGE_FIELD_NAMES,
            "image_field_rows_main": image_field_rows[:6],
            "image_field_rows_extra": image_field_rows[6:],
            "collapsed_defaults": COLLAPSED_DEFAULT_FIELDS,
            "collapsed_fields": [form[name] for name in COLLAPSED_DEFAULT_FIELDS],
            "step_statuses": step_statuses,
            "evergo_so_url": (
                EVERGO_PORTAL_ORDER_URL_TEMPLATE.format(order_id=order.remote_id)
                if order.remote_id is not None
                else ""
            ),
        },
    )


def _load_remote_phase_one_initial_data(
    *, profile: EvergoUser, order_id: int
) -> tuple[dict[str, object], dict[str, str], dict[str, str]]:
    """Load phase-one defaults, field errors, and remote image URLs from order detail payload."""
    try:
        order_payload = profile.fetch_order_detail(order_id=order_id)
    except (EvergoAPIError, OSError):
        return {}, {
            field_name: "No se pudo cargar este dato desde Evergo API. Captúralo manualmente."
            for field_name in TRACKING_PRIMARY_FIELDS
        }, {}

    initial_data = _extract_phase_one_initial_data(order_payload)
    missing_prefill_errors = {
        field_name: "Dato faltante en Evergo API. Captúralo manualmente."
        for field_name in TRACKING_PRIMARY_FIELDS
        if field_name not in initial_data
    }
    return initial_data, missing_prefill_errors, _extract_remote_tracking_image_urls(order_payload)


def _build_image_field_rows(*, form: EvergoOrderTrackingForm, remote_image_urls: dict[str, str]) -> list[dict[str, object]]:
    """Build template rows combining image fields with their remotely stored preview URLs."""
    return [
        {
            "field": form[field_name],
            "remote_url": remote_image_urls.get(field_name, ""),
        }
        for field_name in IMAGE_FIELD_NAMES
    ]


def _build_tracking_step_values(*, values: dict[str, object], remote_image_urls: dict[str, str]) -> dict[str, object]:
    """Merge current form values with persisted remote image URLs for step completion display."""
    step_values = dict(values)
    step_values.update({field_name: url for field_name, url in remote_image_urls.items() if url})
    return step_values


def _extract_remote_tracking_image_urls(order_payload: dict[str, object]) -> dict[str, str]:
    """Extract safe HTTP(S) URLs for tracking image fields from variable Evergo payload shapes."""
    if not isinstance(order_payload, dict):
        return {}

    candidate_sources: list[dict[str, object]] = []
    for key in TRACKING_PREFILL_SOURCE_KEYS:
        source = order_payload.get(key)
        if isinstance(source, dict):
            candidate_sources.append(source)
    candidate_sources.append(order_payload)

    remote_urls: dict[str, str] = {}
    for field_name in IMAGE_FIELD_NAMES:
        for source in candidate_sources:
            normalized_url = _normalize_remote_image_url(value=source.get(field_name))
            if normalized_url:
                remote_urls[field_name] = normalized_url
                break
    return remote_urls


def _normalize_remote_image_url(*, value: object) -> str | None:
    """Normalize remote image URL candidates to safe HTTP(S) absolute URLs."""
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith(("http://", "https://")):
            return candidate
        return None

    if isinstance(value, dict):
        for key in ("url", "file", "path", "imagen", "image", "foto", "archivo"):
            normalized = _normalize_remote_image_url(value=value.get(key))
            if normalized:
                return normalized
        return None

    if isinstance(value, list):
        for item in value:
            normalized = _normalize_remote_image_url(value=item)
            if normalized:
                return normalized

    return None


def _extract_phase_one_initial_data(order_payload: dict[str, object]) -> dict[str, object]:
    """Extract public tracking form-compatible field values from variable WS API structures."""
    if not isinstance(order_payload, dict):
        return {}

    candidate_sources: list[dict[str, object]] = []
    for key in TRACKING_PREFILL_SOURCE_KEYS:
        source = order_payload.get(key)
        if isinstance(source, dict):
            candidate_sources.append(source)
    candidate_sources.append(order_payload)

    initial_data: dict[str, object] = {}
    for field_name in TRACKING_PREFILL_FIELDS:
        value = _first_present_value(candidate_sources=candidate_sources, field_name=field_name)
        if value in (None, ""):
            continue
        normalized = _normalize_tracking_prefill_value(field_name=field_name, value=value)
        if normalized in (None, ""):
            continue
        initial_data[field_name] = normalized

    return initial_data


def _first_present_value(*, candidate_sources: list[dict[str, object]], field_name: str) -> object | None:
    """Return the first non-empty value for a field name from ordered payload dictionaries."""
    for source in candidate_sources:
        if field_name in source and source[field_name] not in (None, ""):
            return source[field_name]
    return None


def _normalize_tracking_prefill_value(*, field_name: str, value: object) -> object | None:
    """Normalize raw WS payload values into form-friendly types."""
    if field_name == "fecha_visita":
        return _normalize_datetime_local_value(value=value)

    if field_name in TRACKING_INT_FIELDS:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    if field_name in TRACKING_DECIMAL_FIELDS:
        try:
            return Decimal(str(value).strip())
        except (InvalidOperation, TypeError, ValueError):
            return None

    return str(value).strip()


def _normalize_datetime_local_value(*, value: object) -> str | None:
    """Convert datetime-like payload values into datetime-local input format."""
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    for pattern in (DATETIME_LOCAL_FORMAT, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw_value, pattern).strftime(DATETIME_LOCAL_FORMAT)
        except ValueError:
            continue

    if raw_value.endswith("Z"):
        raw_value = raw_value[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(raw_value)
        if timezone.is_aware(parsed):
            parsed = timezone.localtime(parsed, timezone.get_default_timezone())
        return parsed.strftime(DATETIME_LOCAL_FORMAT)
    except ValueError:
        return None


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

TRACKING_PREFILL_FIELDS = [
    "calibre_principal",
    "capacidad_itm_principal",
    "concentracion_medidores",
    "fecha_visita",
    "garantia",
    "kit_cfe",
    "marca_cargador",
    "metraje_visita_tecnica",
    "numero_serie",
    "obra_civil",
    "programacion_cargador",
    "prueba_carga",
    "requiere_instalacion",
    "servicio",
    "tipo_inmueble",
    "tipo_visita",
    "voltaje_fase_fase",
    "voltaje_fase_neutro",
    "voltaje_fase_tierra",
    "voltaje_neutro_tierra",
]

TRACKING_PRIMARY_FIELDS = [
    "metraje_visita_tecnica",
    "programacion_cargador",
    "capacidad_itm_principal",
    "fecha_visita",
    "voltaje_fase_fase",
    "voltaje_fase_tierra",
    "voltaje_fase_neutro",
    "voltaje_neutro_tierra",
    "prueba_carga",
    "marca_cargador",
    "numero_serie",
]

STEP_VISITA_REQUIRED_FIELDS = TRACKING_PRIMARY_FIELDS

STEP_ASSIGN_REQUIRED_FIELDS = [
    "fecha_visita",
]

STEP_INSTALL_REQUIRED_FIELDS = TRACKING_PRIMARY_FIELDS + [
    "foto_tablero",
    "foto_medidor",
    "foto_tierra",
    "foto_ruta_cableado",
    "foto_ubicacion_cargador",
    "foto_general",
    "foto_hoja_visita",
    "foto_interruptor_principal",
]

STEP_MONTAJE_REQUIRED_FIELDS = STEP_INSTALL_REQUIRED_FIELDS + [
    "foto_panoramica_estacion",
    "foto_numero_serie_cargador",
    "foto_interruptor_instalado",
    "foto_conexion_cargador",
    "foto_preparacion_cfe",
    "foto_hoja_reporte_instalacion",
    "foto_voltaje_fase_fase",
    "foto_voltaje_fase_tierra",
    "foto_voltaje_fase_neutro",
    "foto_voltaje_neutro_tierra",
]

TRACKING_INT_FIELDS = {
    "metraje_visita_tecnica",
    "capacidad_itm_principal",
}

TRACKING_DECIMAL_FIELDS = {
    "voltaje_fase_fase",
    "voltaje_fase_tierra",
    "voltaje_fase_neutro",
    "voltaje_neutro_tierra",
}


def _has_tracking_value(value: object) -> bool:
    """Return True when a tracking form value should count as filled."""
    return value not in (None, "")


def _is_step_complete(*, values: dict[str, object], required_fields: list[str]) -> bool:
    """Return whether all required fields for a step are present in current values."""
    return all(_has_tracking_value(values.get(field_name)) for field_name in required_fields)


def _compute_tracking_step_completion(cleaned_data: dict[str, object]) -> dict[str, bool]:
    """Compute submit-time step completion gates based on current form payload."""
    visita_complete = _is_step_complete(values=cleaned_data, required_fields=STEP_VISITA_REQUIRED_FIELDS)
    assign_complete = _is_step_complete(values=cleaned_data, required_fields=STEP_ASSIGN_REQUIRED_FIELDS)
    install_fields_complete = _is_step_complete(values=cleaned_data, required_fields=STEP_INSTALL_REQUIRED_FIELDS)
    install_complete = visita_complete and install_fields_complete
    montage_fields_complete = _is_step_complete(values=cleaned_data, required_fields=STEP_MONTAJE_REQUIRED_FIELDS)
    montage_complete = install_complete and montage_fields_complete
    return {
        "visita": visita_complete,
        "assign": assign_complete,
        "install": install_complete,
        "montage": montage_complete,
    }


def _build_tracking_step_statuses(values: dict[str, object]) -> list[dict[str, object]]:
    """Build UI metadata describing which integration steps are currently complete."""
    completion = _compute_tracking_step_completion(values)
    steps = [
        ("visita", "1. Visita técnica"),
        ("assign", "2. Asignar técnico"),
        ("install", "3. Reporte de instalación"),
        ("montage", "4. Montaje-Conexión"),
    ]
    return [
        {
            "key": key,
            "label": label,
            "complete": completion[key],
        }
        for key, label in steps
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
