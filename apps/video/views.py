from django.http import HttpResponseServerError, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render

from .models import MjpegStream


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
        generator = stream.mjpeg_stream()
    except RuntimeError as exc:
        return HttpResponseServerError(str(exc))

    return StreamingHttpResponse(
        generator,
        content_type="multipart/x-mixed-replace; boundary=frame",
    )
