from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from .frame_cache import frame_cache_url, get_frame, get_status, mjpeg_frame_stream
from .models import MjpegStream

import logging

logger = logging.getLogger(__name__)


def stream_detail(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug, is_active=True)
    context = {
        "stream": stream,
        "stream_ws_path": stream.get_stream_ws_path(),
        "stream_webrtc_ws_path": stream.get_webrtc_ws_path(),
        "stream_url": stream.get_stream_url(),
        "webrtc_ice_servers": settings.VIDEO_WEBRTC_ICE_SERVERS,
    }
    return render(request, "video/stream_detail.html", context)


def _build_mjpeg_stream_response(stream: MjpegStream):
    if not frame_cache_url():
        logger.warning("Camera service unavailable for stream %s", stream.slug)
        return HttpResponse("Camera service unavailable.", status=503)

    cached = get_frame(stream)
    if cached:
        generator = mjpeg_frame_stream(stream, first_frame=cached)
        return StreamingHttpResponse(
            generator,
            content_type="multipart/x-mixed-replace; boundary=frame",
        )
    logger.info("No cached frames available for MJPEG stream %s", stream.slug)
    status_payload = get_status(stream)
    if status_payload and status_payload.get("last_error"):
        logger.warning(
            "Camera service error for stream %s: %s",
            stream.slug,
            status_payload.get("last_error"),
        )
    return HttpResponse("Camera service unavailable.", status=503)


def mjpeg_stream(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug, is_active=True)
    return _build_mjpeg_stream_response(stream)


@staff_member_required
def mjpeg_admin_stream(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug)
    return _build_mjpeg_stream_response(stream)


def mjpeg_probe(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug, is_active=True)
    return _build_mjpeg_probe_response(stream)


@staff_member_required
def mjpeg_admin_probe(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug)
    return _build_mjpeg_probe_response(stream)


@staff_member_required
def mjpeg_debug(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug)
    context = {
        "stream": stream,
        "stream_url": stream.get_stream_url(),
        "debug_stream_url": reverse("video:mjpeg-admin-stream", args=[stream.slug]),
        "status_url": reverse("video:mjpeg-debug-status", args=[stream.slug]),
        "probe_url": reverse("video:mjpeg-admin-probe", args=[stream.slug]),
    }
    return render(request, "video/mjpeg_debug.html", context)


@staff_member_required
def mjpeg_debug_status(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug)
    data = {
        "server_time": timezone.now().isoformat(),
        "stream": {
            "name": stream.name,
            "slug": stream.slug,
            "is_active": stream.is_active,
        },
        "video_device": {
            "id": stream.video_device_id,
            "name": stream.video_device.display_name,
            "identifier": stream.video_device.identifier,
        },
        "last_frame_captured_at": _format_timestamp(stream.last_frame_captured_at),
        "last_thumbnail_at": _format_timestamp(stream.last_thumbnail_at),
        "last_frame_sample_id": stream.last_frame_sample_id,
        "last_thumbnail_sample_id": stream.last_thumbnail_sample_id,
        "camera_service": get_status(stream) if frame_cache_url() else None,
    }
    return JsonResponse(data)


def _format_timestamp(value):
    if not value:
        return None
    return timezone.localtime(value).isoformat()


def _build_mjpeg_probe_response(stream: MjpegStream):
    if not frame_cache_url():
        logger.warning("Camera service unavailable for probe %s", stream.slug)
        return HttpResponse("Camera service unavailable.", status=503)

    cached = get_frame(stream)
    if cached:
        try:
            stream.store_frame_bytes(cached.frame_bytes, update_thumbnail=True)
        except Exception:
            logger.exception("Unable to store cached MJPEG frame for %s", stream.slug)
            return HttpResponse("Unable to store frame.", status=503)
        return HttpResponse(status=204)
    logger.info("No cached frames available for probe %s", stream.slug)
    status_payload = get_status(stream)
    if status_payload and status_payload.get("last_error"):
        logger.warning(
            "Camera service error for probe %s: %s",
            stream.slug,
            status_payload.get("last_error"),
        )
    return HttpResponse("Camera service unavailable.", status=503)


def camera_gallery(request):
    streams = (
        MjpegStream.objects.filter(is_active=True)
        .select_related("last_thumbnail_sample", "last_frame_sample")
        .order_by("name")
    )
    context = {"streams": streams}
    return render(request, "video/camera_gallery.html", context)
