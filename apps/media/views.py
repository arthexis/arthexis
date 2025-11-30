from django.core.files.uploadedfile import UploadedFile
from django.http import HttpResponseGone, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import MediaBucket, MediaFile


def _first_file(files: dict[str, object]) -> UploadedFile | None:
    for value in files.values():
        if isinstance(value, UploadedFile):
            return value
        if hasattr(value, "read"):
            return value  # type: ignore[return-value]
    return None


@csrf_exempt
def media_bucket_upload(request, slug):
    bucket = get_object_or_404(MediaBucket, slug=slug)
    if bucket.is_expired(reference=timezone.now()):
        return HttpResponseGone()

    if request.method not in {"POST", "PUT"}:
        return HttpResponseNotAllowed(["POST", "PUT"])

    if not request.FILES:
        return JsonResponse({"detail": "file is required"}, status=400)

    uploaded_file = request.FILES.get("file") or _first_file(request.FILES)
    if uploaded_file is None:
        return JsonResponse({"detail": "file is required"}, status=400)

    filename = getattr(uploaded_file, "name", "")
    if not bucket.allows_filename(filename):
        return JsonResponse({"detail": "file type is not allowed"}, status=400)

    size = getattr(uploaded_file, "size", 0) or 0
    if not bucket.allows_size(size):
        return JsonResponse({"detail": "file exceeds size limits"}, status=400)

    media_file = MediaFile(
        bucket=bucket,
        file=uploaded_file,
        original_name=filename,
        content_type=getattr(uploaded_file, "content_type", "") or "",
        size=size,
    )
    media_file.save()

    return JsonResponse(
        {
            "status": "ok",
            "name": media_file.original_name,
            "url": media_file.file.url,
            "size": media_file.size,
        },
        status=201,
    )
