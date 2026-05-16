from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.nodes.models import Node, NodeRole


@pytest.mark.django_db
def test_summary_enabled_requires_control_node(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()
    role = NodeRole.objects.create(name="Terminal")
    node = Node.objects.create(
        hostname="terminal",
        public_endpoint="terminal",
        role=role,
    )
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)

    with pytest.raises(CommandError, match="only be enabled on Control nodes"):
        call_command("summary", "--enabled")
