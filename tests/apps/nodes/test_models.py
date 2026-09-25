import pytest

from apps.nodes.models import Node, NodeLink, NodeRole

pytestmark = pytest.mark.django_db


def test_node_roles_and_topology_are_persisted() -> None:
    control = Node.objects.create(
        identifier="control-1",
        display_name="Control",
        role=NodeRole.CONTROL,
    )
    terminal = Node.objects.create(
        identifier="terminal-1",
        display_name="Terminal",
        role=NodeRole.TERMINAL,
    )
    link = NodeLink.objects.create(source=control, target=terminal)

    assert link.source.role == NodeRole.CONTROL
    assert control.links.get() == link
