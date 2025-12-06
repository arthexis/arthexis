from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from ..models import CPFirmwareDeployment


def firmware_download(request, deployment_id: int, token: str):
    deployment = get_object_or_404(
        CPFirmwareDeployment,
        pk=deployment_id,
        download_token=token,
    )
    expires = deployment.download_token_expires_at
    if expires and timezone.now() > expires:
        return HttpResponse(status=403)
    firmware = deployment.firmware
    if firmware is None:
        raise Http404
    payload = firmware.get_payload_bytes()
    if not payload:
        raise Http404
    content_type = firmware.content_type or "application/octet-stream"
    response = HttpResponse(payload, content_type=content_type)
    filename = firmware.filename or f"firmware_{firmware.pk or deployment.pk}"
    safe_filename = filename.replace("\r", "").replace("\n", "").replace("\"", "")
    response["Content-Disposition"] = f'attachment; filename="{safe_filename}"'
    response["Content-Length"] = str(len(payload))
    deployment.downloaded_at = timezone.now()
    deployment.save(update_fields=["downloaded_at", "updated_at"])
    return response
