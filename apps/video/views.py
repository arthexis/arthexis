from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from .frame_cache import get_frame, get_status, mjpeg_frame_stream
from .models import MjpegDependencyError, MjpegDeviceUnavailableError, MjpegStream

import logging

logger = logging.getLogger(__name__)


def _is_missing_mjpeg_dependency(exc: Exception) -> bool:
    return "OpenCV (cv2)" in str(exc)


def stream_detail(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug, is_active=True)
    context = {
        "stream": stream,
        "stream_url": stream.get_stream_url(),
    }
    return render(request, "video/stream_detail.html", context)


def _build_direct_mjpeg_stream_response(stream: MjpegStream):
    try:
        frame_iter = stream.iter_frame_bytes()
        first_frame = next(frame_iter)
    except StopIteration:
        logger.info("No frames available for MJPEG stream %s", stream.slug)
        return HttpResponse(status=204)
    except MjpegDependencyError:
        logger.warning("MJPEG dependencies unavailable for stream %s", stream.slug)
        return HttpResponse(status=204)
    except MjpegDeviceUnavailableError:
        logger.info("MJPEG device unavailable for stream %s", stream.slug)
        return HttpResponse(status=204)
    except RuntimeError as exc:
        if _is_missing_mjpeg_dependency(exc):
            logger.warning("MJPEG dependencies unavailable for stream %s", stream.slug)
            return HttpResponse(status=204)
        logger.exception("Runtime error while starting MJPEG stream %s", stream.slug)
        return HttpResponse("Unable to start stream.", status=503)
    except Exception as exc:
        logger.exception("Unexpected error while starting MJPEG stream %s", stream.slug)
        return HttpResponse("Unable to start stream.", status=503)

    generator = stream.mjpeg_stream(frame_iter, first_frame=first_frame)

    return StreamingHttpResponse(
        generator,
        content_type="multipart/x-mixed-replace; boundary=frame",
    )


def _build_mjpeg_stream_response(stream: MjpegStream):
    if settings.VIDEO_FRAME_REDIS_URL:
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
        logger.info("Falling back to direct MJPEG capture for stream %s", stream.slug)
        return _build_direct_mjpeg_stream_response(stream)

    return _build_direct_mjpeg_stream_response(stream)


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
        "camera_service": get_status(stream) if settings.VIDEO_FRAME_REDIS_URL else None,
    }
    return JsonResponse(data)


def _format_timestamp(value):
    if not value:
        return None
    return timezone.localtime(value).isoformat()


def _build_mjpeg_probe_response(stream: MjpegStream):
    if settings.VIDEO_FRAME_REDIS_URL:
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

    try:
        frame_bytes = stream.capture_frame_bytes()
    except (MjpegDependencyError, MjpegDeviceUnavailableError, RuntimeError) as exc:
        if isinstance(exc, MjpegDependencyError) or _is_missing_mjpeg_dependency(exc):
            logger.warning("MJPEG dependencies unavailable for probe %s", stream.slug)
            return HttpResponse(status=204)
        if isinstance(exc, MjpegDeviceUnavailableError):
            logger.info("MJPEG device unavailable for probe %s", stream.slug)
            return HttpResponse(status=204)
        logger.exception("Runtime error while capturing MJPEG frame for %s", stream.slug)
        return HttpResponse("Unable to capture frame.", status=503)
    except Exception:
        logger.exception("Unexpected error while capturing MJPEG frame for %s", stream.slug)
        return HttpResponse("Unable to capture frame.", status=503)

    if frame_bytes:
        try:
            stream.store_frame_bytes(frame_bytes, update_thumbnail=True)
        except Exception:
            logger.exception("Unable to store MJPEG frame for %s", stream.slug)
            return HttpResponse("Unable to store frame.", status=503)

    return HttpResponse(status=204)


def camera_gallery(request):
    streams = (
        MjpegStream.objects.filter(is_active=True)
        .select_related("last_thumbnail_sample", "last_frame_sample")
        .order_by("name")
    )
    context = {"streams": streams}
    return render(request, "video/camera_gallery.html", context)
