"""Public views for Evergo customer profiles."""

from __future__ import annotations

from urllib.parse import quote_plus

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .exceptions import EvergoAPIError
from .forms import EvergoOrderTrackingForm
from .models import EvergoArtifact, EvergoCustomer, EvergoOrder
from .services import ensure_image_payload


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


def order_tracking_public(request, order_id: int) -> HttpResponse:
    """Render and submit the public order tracking phase-one helper form."""
    order = get_object_or_404(EvergoOrder.objects.select_related("user"), remote_id=order_id)
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
                try:
                    profile.submit_tracking_phase_one(order_id=order_id, payload=payload, files=files)
                except EvergoAPIError as exc:
                    form.add_error(None, str(exc))
                else:
                    messages.success(request, "Orden enviada correctamente en las tres fases de API.")
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
