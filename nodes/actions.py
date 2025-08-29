from __future__ import annotations

from typing import Dict, Iterable, Optional, Type

from .models import Node


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


