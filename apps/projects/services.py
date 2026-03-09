"""ZIP import/export services for project bundles."""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.core.serializers import deserialize, serialize
from django.db import transaction
from django.http import HttpResponse

from apps.projects.models import Project, ProjectItem


ARCHIVE_PROJECT_FILE = "project.json"
ARCHIVE_OBJECTS_FILE = "objects.json"
ARCHIVE_ITEMS_FILE = "items.json"


def _safe_archive_filename(value: str) -> str:
    """Return a filesystem-safe archive filename stem."""

    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return sanitized or "project"


def build_project_bundle_response(project: Project) -> HttpResponse:
    """Return a ZIP response containing project metadata and serialized objects."""

    items = list(project.items.select_related("content_type"))
    grouped_ids: dict[type, set[str]] = defaultdict(set)
    item_payload = []

    for item in items:
        model_class = item.content_type.model_class()
        if model_class is None:
            continue
        grouped_ids[model_class].add(str(item.object_id))
        item_payload.append(
            {
                "model": f"{item.content_type.app_label}.{item.content_type.model}",
                "object_id": str(item.object_id),
                "note": item.note,
            }
        )

    serialized_objects = []
    for model_class, object_ids in grouped_ids.items():
        objects = model_class._default_manager.filter(pk__in=object_ids)
        serialized_objects.extend(json.loads(serialize("json", objects)))

    project_payload = {
        "name": project.name,
        "description": project.description,
    }

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(ARCHIVE_PROJECT_FILE, json.dumps(project_payload, indent=2))
        bundle.writestr(ARCHIVE_OBJECTS_FILE, json.dumps(serialized_objects, indent=2))
        bundle.writestr(ARCHIVE_ITEMS_FILE, json.dumps(item_payload, indent=2))

    filename = _safe_archive_filename(project.name)
    response = HttpResponse(archive.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}.zip"'
    return response


@transaction.atomic
def import_project_bundle(project: Project, bundle_file) -> tuple[int, int]:
    """Import a project archive into ``project`` and return (objects, links) counts."""

    with zipfile.ZipFile(bundle_file) as bundle:
        objects_payload = json.loads(bundle.read(ARCHIVE_OBJECTS_FILE).decode("utf-8"))
        items_payload = json.loads(bundle.read(ARCHIVE_ITEMS_FILE).decode("utf-8"))

    imported_objects = 0
    for deserialized in deserialize("json", json.dumps(objects_payload)):
        deserialized.save()
        imported_objects += 1

    linked = 0
    for item_data in items_payload:
        model_label = item_data.get("model", "")
        if "." not in model_label:
            continue
        app_label, model_name = model_label.split(".", 1)
        try:
            content_type = ContentType.objects.get_by_natural_key(app_label, model_name)
        except ContentType.DoesNotExist:
            continue
        _, created = ProjectItem.objects.get_or_create(
            project=project,
            content_type=content_type,
            object_id=str(item_data.get("object_id", "")),
            defaults={"note": item_data.get("note", "")},
        )
        if created:
            linked += 1

    return imported_objects, linked
