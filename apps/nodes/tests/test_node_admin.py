from django.contrib.admin.sites import AdminSite

from apps.nodes.admin.node_admin import NodeAdmin
from apps.nodes.models import Node


def _node_admin() -> NodeAdmin:
    return NodeAdmin(Node, AdminSite())


def test_node_admin_changelist_omits_mesh_status_columns() -> None:
    admin = _node_admin()

    assert "relation" in admin.list_display
    assert "mesh_status_badge" not in admin.list_display
    assert "last_mesh_heartbeat" not in admin.list_display


def test_node_admin_relation_uses_short_label_with_full_tooltip() -> None:
    admin = _node_admin()
    node = Node(hostname="downstream-node", current_relation=Node.Relation.DOWNSTREAM)

    html = str(admin.relation(node))

    assert 'title="Downstream"' in html
    assert html.endswith(" Down</span>")
    assert "Downstream</span>" not in html
