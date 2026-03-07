"""Archive helpers for JavaScript extensions."""

from __future__ import annotations

import io
import json
import re
import zipfile

from django.http import HttpResponse

from apps.extensions.models import JsExtension


def build_extension_archive_response(extension: JsExtension) -> HttpResponse:
    """Return a ZIP response containing generated extension files."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for filename, contents in extension.build_extension_archive_files().items():
            payload = (
                json.dumps(contents, indent=2)
                if isinstance(contents, dict)
                else str(contents)
            )
            bundle.writestr(filename, payload)

    archive.seek(0)
    response = HttpResponse(archive.getvalue(), content_type="application/zip")

    raw_name = f"{extension.slug}-{extension.version}.zip"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._")
    if not safe_name:
        safe_name = "extension.zip"
    response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return response


__all__ = ["build_extension_archive_response"]
