from django.test import TestCase

from apps.nodes.models import Node, NodeLink, NodeRole


class NodeModelTests(TestCase):
    def test_node_roles_and_topology_are_persisted(self) -> None:
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

        self.assertEqual(link.source.role, NodeRole.CONTROL)
        self.assertEqual(control.links.get(), link)
