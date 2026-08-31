import mimetypes
import posixpath

from django.apps import apps
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Q
from django.http import (
    FileResponse,
    Http404,
    HttpResponseForbidden,
    HttpResponseGone,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_safe
from django.views.static import serve as serve_static

from .models import MediaBucket, MediaFile

PUBLIC_MEDIA_PREFIXES = (
    "ocpp/public_pages/qr/",
)


def _first_file(files: dict[str, object]) -> UploadedFile | None:
    for value in files.values():
        if isinstance(value, UploadedFile):
            return value
        if hasattr(value, "read"):
            return value  # type: ignore[return-value]
    return None


def _normalize_media_path(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/")).lstrip("/")
    if not normalized or normalized == "." or normalized.startswith("../"):
        raise Http404
    if "\x00" in normalized:
        raise Http404
    return normalized


def _configured_model(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def _is_active_staff_user(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
    )


def _model_references_media(
    *, app_label: str, model_name: str, field_name: str, media_file: MediaFile
) -> bool:
    model = _configured_model(app_label, model_name)
    if model is None:
        return False
    return model.objects.filter(**{field_name: media_file}).exists()


def _model_references_file_path(
    *,
    app_label: str,
    model_name: str,
    field_name: str,
    path: str,
    extra_filters: dict[str, object] | None = None,
) -> bool:
    model = _configured_model(app_label, model_name)
    if model is None:
        return False
    filters = {field_name: path}
    if extra_filters:
        filters.update(extra_filters)
    return model.objects.filter(**filters).exists()


def _reference_image_media_access(media_file: MediaFile, request) -> bool | None:
    return None


def _is_public_media_file(media_file: MediaFile) -> bool:
    station_model = _configured_model("ocpp", "StationModel")
    if (
        station_model is not None
        and station_model.objects.filter(
            Q(images_bucket=media_file.bucket) | Q(documents_bucket=media_file.bucket)
        ).exists()
    ):
        return True

    if _model_references_media(
        app_label="modules",
        model_name="Module",
        field_name="favicon_media",
        media_file=media_file,
    ):
        return True

    if _model_references_media(
        app_label="pages",
        model_name="SiteBadge",
        field_name="favicon_media",
        media_file=media_file,
    ):
        return True

    return False


def _is_public_direct_file_path(path: str) -> bool:
    return _model_references_file_path(
        app_label="chats",
        model_name="ChatAvatar",
        field_name="photo",
        path=path,
        extra_filters={"is_enabled": True},
    )


def _can_serve_media_file(media_file: MediaFile, request) -> bool:
    user = getattr(request, "user", None)
    if _is_public_media_file(media_file):
        return True

    reference_access = _reference_image_media_access(media_file, request)
    if reference_access is not None:
        return reference_access

    return _is_active_staff_user(user)


def _can_serve_media_path(request, path: str) -> bool:
    user = getattr(request, "user", None)
    if any(path.startswith(prefix) for prefix in PUBLIC_MEDIA_PREFIXES):
        return True

    for media_file in MediaFile.objects.select_related("bucket").filter(file=path):
        if _can_serve_media_file(media_file, request):
            return True

    if _is_public_direct_file_path(path):
        return True

    return _is_active_staff_user(user)


@require_safe
def serve_media_file(request, path: str):
    path = _normalize_media_path(path)
    if getattr(settings, "DEBUG", False):
        return serve_static(request, path, document_root=settings.MEDIA_ROOT)

    if not _can_serve_media_path(request, path):
        return HttpResponseForbidden()

    if not default_storage.exists(path):
        raise Http404

    content_type, _encoding = mimetypes.guess_type(path)
    response = FileResponse(
        default_storage.open(path, "rb"),
        content_type=content_type or "application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@csrf_exempt
def media_bucket_upload(request, slug):
    bucket = get_object_or_404(MediaBucket, slug=slug)
    if bucket.is_expired(reference=timezone.now()):
        return HttpResponseGone()

    if request.method not in {"POST", "PUT"}:
        return HttpResponseNotAllowed(["POST", "PUT"])

    if bucket.expires_at is None and not (
        request.user.is_authenticated and request.user.is_active
    ):
        return JsonResponse(
            {"detail": "authentication is required for this bucket"},
            status=403,
        )

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
