from __future__ import annotations

from typing import Dict, Iterable, Optional, Type

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.http import HttpResponse
from django.utils import timezone
from pathlib import Path
from django.db import connection

from .models import Backup, Node


class NodeAction:
    """Base class for actions that operate on a :class:`~nodes.models.Node`."""

    #: Human friendly name for this action
    display_name: str = ""
    #: Short slug used in URLs
    slug: str = ""
    #: Description of the action
    description: str = ""
    #: Whether this action supports running on remote nodes
    supports_remote: bool = False

    # registry of available actions
    registry: Dict[str, Type["NodeAction"]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.slug:
            key = cls.slug
        else:
            key = cls.__name__.lower()
            cls.slug = key
        NodeAction.registry[key] = cls

    @classmethod
    def get_actions(cls) -> Iterable[Type["NodeAction"]]:
        """Return all registered node actions."""
        return cls.registry.values()

    @classmethod
    def run(cls, node: Optional[Node] = None, **kwargs):
        """Execute this action on ``node``.

        If ``node`` is ``None`` the local node is used. If the target node is
        not the local host and ``supports_remote`` is ``False``, a
        ``NotImplementedError`` is raised.
        """

        if node is None:
            node = Node.get_local()
        if node is None:
            raise ValueError("No local node configured")
        if not node.is_local and not cls.supports_remote:
            raise NotImplementedError("Remote node actions are not yet implemented")
        instance = cls()
        return instance.execute(node, **kwargs)

    def execute(self, node: Node, **kwargs):  # pragma: no cover - interface
        """Perform the action on ``node``."""
        raise NotImplementedError


class CaptureScreenshotAction(NodeAction):
    display_name = "Take Site Screenshot"
    slug = "capture-screenshot"

    def execute(self, node: Node, **kwargs):  # pragma: no cover - uses selenium
        from .utils import capture_screenshot, save_screenshot

        url = f"http://{node.address}:{node.port}"
        path = capture_screenshot(url)
        save_screenshot(path, node=node, method="NODE_ACTION")
        return path


class GenerateBackupAction(NodeAction):
    display_name = "Generate DB Backup"
    slug = "generate-db-backup"

    def execute(self, node: Node, **kwargs):
        base_path = Path(node.base_path or settings.BASE_DIR)
        backup_dir = base_path / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        filename = f"backup-{timezone.now().strftime('%Y%m%d%H%M%S')}.json"
        file_path = backup_dir / filename
        with file_path.open("w", encoding="utf-8") as fh:
            exclude = []
            if apps.is_installed("emails"):
                exclude.append("emails")
            if apps.is_installed("post_office") and "emailpattern" in apps.all_models.get(
                "post_office", {}
            ):
                exclude.append("post_office.emailpattern")
            call_command("dumpdata", exclude=exclude, stdout=fh)
        size = file_path.stat().st_size
        tables = set(connection.introspection.table_names())
        objects = 0
        for model in apps.get_models():
            if model._meta.db_table in tables:
                objects += model.objects.count()
        report = {"objects": objects}
        Backup.objects.create(
            location=str(file_path.relative_to(base_path)), size=size, report=report
        )
        data = file_path.read_bytes()
        response = HttpResponse(data, content_type="application/json")
        response["Content-Disposition"] = f"attachment; filename={filename}"
        return response
