from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render

from .models import MjpegStream

import logging

logger = logging.getLogger(__name__)


def stream_detail(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug, is_active=True)
    context = {
        "stream": stream,
        "stream_url": stream.get_stream_url(),
    }
    return render(request, "video/stream_detail.html", context)


def mjpeg_stream(request, slug):
    stream = get_object_or_404(MjpegStream, slug=slug, is_active=True)

    try:
        frame_iter = stream.iter_frame_bytes()
        first_frame = next(frame_iter)
    except StopIteration:
        logger.info("No frames available for MJPEG stream %s", slug)
        return HttpResponse(status=204)
    except RuntimeError as exc:
        logger.exception("Runtime error while starting MJPEG stream %s", slug)
        return HttpResponse("Unable to start stream.", status=503)
    except Exception as exc:
        logger.exception("Unexpected error while starting MJPEG stream %s", slug)
        return HttpResponse("Unable to start stream.", status=503)

    generator = stream.mjpeg_stream(frame_iter, first_frame=first_frame)

    return StreamingHttpResponse(
        generator,
        content_type="multipart/x-mixed-replace; boundary=frame",
    )


def camera_gallery(request):
    streams = (
        MjpegStream.objects.filter(is_active=True)
        .select_related("last_thumbnail_sample", "last_frame_sample")
        .order_by("name")
    )
    context = {"streams": streams}
    return render(request, "video/camera_gallery.html", context)
