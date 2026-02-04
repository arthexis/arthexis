from __future__ import annotations

from pathlib import Path

from django.contrib.admin.views.decorators import staff_member_required
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache

from apps.nginx.models import SiteConfiguration


def _resolve_default_certificate() -> tuple[Path, str] | None:
    config = SiteConfiguration.get_default()
    certificate = config.certificate
    if certificate is None:
        return None
    if not certificate.certificate_path:
        return None
    path = Path(certificate.certificate_path)
    if not path.exists():
        return None
    return path, certificate.domain


@staff_member_required
@never_cache
def trust_certificate(request):
    resolved = _resolve_default_certificate()
    context = {
        "certificate_available": resolved is not None,
        "certificate_domain": resolved[1] if resolved else "",
    }
    return render(request, "certs/trust.html", context)


@staff_member_required
@never_cache
def trust_certificate_download(request):
    resolved = _resolve_default_certificate()
    if not resolved:
        raise Http404(_("No certificate is available to download."))
    path, domain = resolved
    filename = f"{domain or 'certificate'}.pem"
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type="application/x-x509-ca-cert",
    )
