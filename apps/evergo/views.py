"""Public views for Evergo customer profiles."""

from __future__ import annotations

import base64
import mimetypes
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import urlopen

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.features.utils import is_pages_chat_runtime_enabled, is_suite_feature_enabled
from apps.reports.services import _render_pdf_bytes
from apps.sites.context_processors import _build_chat_context

from .exceptions import EvergoAPIError, EvergoPhaseSubmissionError
from .forms import EvergoCustomerImageUploadForm, EvergoDashboardLookupForm, EvergoOrderTrackingForm
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
EVERGO_PUBLIC_IMAGE_LIMIT = 10
EVERGO_PUBLIC_IMAGE_TOTAL_STORAGE_LIMIT = 20 * 1024 * 1024

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


def _parse_user_story_attachment_limit() -> int:
    """Return the configured attachment limit with a safe integer fallback.

    Returns:
        int: Parsed attachment limit, or ``3`` when the setting is invalid.
    """

    raw_limit = getattr(settings, "USER_STORY_ATTACHMENT_LIMIT", 3)
    try:
        return int(raw_limit)
    except (TypeError, ValueError):
        return 3


def _build_public_widget_context(*, user) -> dict[str, object]:
    """Return chat and feedback flags for Evergo public pages.

    Parameters:
        user: Current authenticated Django user viewing the page.

    Returns:
        dict[str, object]: Template context flags required by shared public widgets.
    """

    feedback_ingestion_enabled = is_suite_feature_enabled("feedback-ingestion", default=True)
    pages_chat_enabled = is_pages_chat_runtime_enabled(default=False)
    site = Site.objects.get_current()
    chat_context = _build_chat_context(
        site,
        user,
        pages_chat_enabled=pages_chat_enabled,
    )
    return {
        "chat_enabled": chat_context["chat_enabled"],
        "chat_socket_path": chat_context["chat_socket_path"],
        "feedback_ingestion_enabled": feedback_ingestion_enabled,
        "user_story_attachment_limit": _parse_user_story_attachment_limit(),
    }


def _normalize_display_text(value: str | None, *, default: str = "-") -> str:
    """Normalize common encoding artifacts in user-facing Evergo text."""
    normalized = str(value or "").strip()
    if not normalized:
        return default
    return DISPLAY_TEXT_FIXUPS.get(normalized, normalized)


def _get_evergo_public_image_limit() -> int:
    """Return the configured public image limit with safe parsing."""
    try:
        return int(getattr(settings, "EVERGO_PUBLIC_IMAGE_LIMIT", EVERGO_PUBLIC_IMAGE_LIMIT))
    except (TypeError, ValueError):
        return EVERGO_PUBLIC_IMAGE_LIMIT


def _get_evergo_public_image_total_storage_limit() -> int:
    """Return the total image storage cap in bytes with safe parsing."""
    try:
        return int(
            getattr(
                settings,
                "EVERGO_PUBLIC_IMAGE_TOTAL_STORAGE_LIMIT",
                EVERGO_PUBLIC_IMAGE_TOTAL_STORAGE_LIMIT,
            )
        )
    except (TypeError, ValueError):
        return EVERGO_PUBLIC_IMAGE_TOTAL_STORAGE_LIMIT


def _build_customer_maps_context(customer: EvergoCustomer) -> dict[str, str]:
    """Return public map URLs for the customer location."""
    address = customer.address.strip()
    if not address:
        return {
            "google_maps_url": "",
            "google_maps_embed_url": "",
            "google_maps_snapshot_url": "",
        }
    encoded_address = quote_plus(address)
    static_map_api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "").strip()
    static_map_key_fragment = f"&key={quote_plus(static_map_api_key)}" if static_map_api_key else ""
    return {
        "google_maps_url": f"https://www.google.com/maps/search/?api=1&query={encoded_address}",
        "google_maps_embed_url": f"https://maps.google.com/maps?q={encoded_address}&z=15&output=embed",
        "google_maps_snapshot_url": (
            "https://maps.googleapis.com/maps/api/staticmap"
            f"?center={encoded_address}&zoom=15&size=1200x600&markers=color:red%7C{encoded_address}"
            f"{static_map_key_fragment}"
        ),
    }


def _resequence_customer_image_artifacts(customer: EvergoCustomer) -> None:
    """Ensure image artifacts have contiguous display_order values."""
    image_artifacts = list(customer.artifacts.filter(artifact_type=EvergoArtifact.ARTIFACT_TYPE_IMAGE))
    updates: list[EvergoArtifact] = []
    for index, artifact in enumerate(image_artifacts, start=1):
        if artifact.display_order != index:
            artifact.display_order = index
            updates.append(artifact)
    if updates:
        EvergoArtifact.objects.bulk_update(updates, ["display_order"])


def _to_data_uri(content: bytes, *, content_type: str) -> str:
    """Return a base64 data URI for PDF rendering."""
    return f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"


def _artifact_image_data_uri(artifact: EvergoArtifact) -> str:
    """Return a data URI for an artifact image file."""
    guessed_type, _ = mimetypes.guess_type(artifact.file.name)
    content_type = guessed_type or "application/octet-stream"
    artifact.file.open("rb")
    try:
        content = artifact.file.read()
    finally:
        artifact.file.close()
    return _to_data_uri(content, content_type=content_type)


def _remote_image_data_uri(url: str) -> str:
    """Fetch remote image bytes and return a data URI, or an empty string on failure."""
    if not url:
        return ""
    try:
        with urlopen(url, timeout=5) as response:
            content = response.read()
            content_type = response.headers.get_content_type() or "application/octet-stream"
    except (OSError, URLError, ValueError):
        return ""
    return _to_data_uri(content, content_type=content_type)


def _delete_artifact_and_blob(artifact: EvergoArtifact) -> None:
    """Delete an artifact and its underlying storage blob."""
    artifact.file.delete(save=False)
    artifact.delete()


def customer_public_detail(request, public_id) -> HttpResponse:
    """Render a shareable Evergo customer profile and handle temporary image uploads."""
    customer = get_object_or_404(
        EvergoCustomer.objects.select_related("latest_order", "user__user"),
        public_id=public_id,
    )
    _resequence_customer_image_artifacts(customer)
    artifacts = list(customer.artifacts.all())
    image_artifacts = [artifact for artifact in artifacts if artifact.is_image]
    image_limit = _get_evergo_public_image_limit()
    storage_limit_bytes = _get_evergo_public_image_total_storage_limit()
    current_storage_bytes = sum(artifact.file.size for artifact in image_artifacts)

    upload_form = EvergoCustomerImageUploadForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "upload-image":
            upload_form = EvergoCustomerImageUploadForm(request.POST, request.FILES)
            if upload_form.is_valid():
                if len(image_artifacts) >= image_limit:
                    upload_form.add_error("image", f"You can only add up to {image_limit} images.")
                else:
                    uploaded_image = upload_form.cleaned_data["image"]
                    projected_total = current_storage_bytes + uploaded_image.size
                    if projected_total > storage_limit_bytes:
                        upload_form.add_error(
                            "image",
                            (
                                "Image storage limit reached. "
                                f"Allowed total: {storage_limit_bytes // (1024 * 1024)} MB."
                            ),
                        )
                    else:
                        next_order = max((artifact.display_order for artifact in image_artifacts), default=0) + 1
                        try:
                            EvergoArtifact.objects.create(
                                customer=customer,
                                file=uploaded_image,
                                artifact_type=EvergoArtifact.ARTIFACT_TYPE_IMAGE,
                                display_order=next_order,
                            )
                        except ValidationError as exc:
                            for message in exc.message_dict.get("file", []):
                                upload_form.add_error("image", message)
                        else:
                            messages.success(request, "Image added.")
                            return redirect(customer.get_absolute_url())
        elif action == "delete-image":
            artifact_id = request.POST.get("artifact_id")
            if request.POST.get("confirm_delete") != "yes":
                messages.error(request, "Deletion cancelled because confirmation was missing.")
            elif not str(artifact_id or "").isdigit():
                raise Http404("Image not found.")
            else:
                artifact = get_object_or_404(
                    EvergoArtifact,
                    pk=artifact_id,
                    customer=customer,
                    artifact_type=EvergoArtifact.ARTIFACT_TYPE_IMAGE,
                )
                _delete_artifact_and_blob(artifact)
                _resequence_customer_image_artifacts(customer)
                messages.success(request, "Image deleted.")
                return redirect(customer.get_absolute_url())

    maps_context = _build_customer_maps_context(customer)

    context = {
        "customer": customer,
        "image_artifacts": image_artifacts,
        "max_images_reached": len(image_artifacts) >= image_limit,
        "upload_form": upload_form,
        "remaining_storage_mb": max(0, (storage_limit_bytes - current_storage_bytes) // (1024 * 1024)),
        **maps_context,
    }
    return render(request, "evergo/customer_public_detail.html", context)


def customer_pdf_download(request, public_id) -> HttpResponse:
    """Generate and download a PDF from the public customer page content."""
    customer = get_object_or_404(
        EvergoCustomer.objects.select_related("latest_order"),
        public_id=public_id,
    )
    _resequence_customer_image_artifacts(customer)
    image_artifacts = list(customer.artifacts.filter(artifact_type=EvergoArtifact.ARTIFACT_TYPE_IMAGE))
    maps_context = _build_customer_maps_context(customer)
    map_snapshot_data_uri = _remote_image_data_uri(maps_context.get("google_maps_snapshot_url", ""))
    html = render_to_string(
        "evergo/customer_public_pdf.html",
        {
            "customer": customer,
            "image_artifacts": [
                {
                    "name": artifact.filename,
                    "url": _artifact_image_data_uri(artifact),
                }
                for artifact in image_artifacts
            ],
            "google_maps_snapshot_data_uri": map_snapshot_data_uri,
            **maps_context,
        },
    )
    payload = _render_pdf_bytes(html)
    if not payload:
        raise Http404("PDF renderer is unavailable.")
    safe_so = customer.latest_so or str(customer.public_id)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="evergo-{safe_so}.pdf"'
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
    if normalized.lstrip().startswith(("=", "+", "-", "@")):
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
    order_lookup = {
        "remote_id": order_id,
    }
    if not request.user.is_staff:
        order_lookup["user__user"] = request.user
    order = get_object_or_404(EvergoOrder.objects.select_related("user"), **order_lookup)
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
                image_inputs: dict[str, object] = {}
                for name in IMAGE_FIELD_NAMES:
                    image_value = form.cleaned_data.get(name)
                    if image_value is not None:
                        image_inputs[name] = image_value
                    elif not remote_image_urls.get(name):
                        image_inputs[name] = None
                files = ensure_image_payload(image_inputs)
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
            **_build_public_widget_context(user=request.user),
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
