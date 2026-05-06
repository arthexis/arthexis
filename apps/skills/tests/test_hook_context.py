from __future__ import annotations

import json

import pytest
from django.core.management import call_command

from apps.nodes.models import Node, NodeRole
from apps.skills.hook_context import list_hooks
from apps.skills.models import Hook

pytestmark = [pytest.mark.django_db]


def test_list_hooks_filters_by_event_platform_and_node_role():
    role = NodeRole.objects.create(name="Terminal")
    other_role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(hostname="local-node", role=role)
    matching = Hook.objects.create(
        slug="terminal-start",
        title="Terminal Start",
        event=Hook.Event.SESSION_START,
        platform=Hook.Platform.WINDOWS,
        command="python manage.py check --fail-level ERROR",
    )
    matching.node_roles.add(role)
    blocked = Hook.objects.create(
        slug="control-start",
        title="Control Start",
        event=Hook.Event.SESSION_START,
        platform=Hook.Platform.WINDOWS,
        command="python manage.py check --fail-level ERROR",
    )
    blocked.node_roles.add(other_role)
    Hook.objects.create(
        slug="linux-start",
        title="Linux Start",
        event=Hook.Event.SESSION_START,
        platform=Hook.Platform.LINUX,
        command="python manage.py check --fail-level ERROR",
    )

    hooks = list_hooks(
        event=Hook.Event.SESSION_START,
        platform=Hook.Platform.WINDOWS,
        node=node,
    )

    assert [hook["slug"] for hook in hooks] == ["terminal-start"]


def test_codex_hooks_command_lists_enabled_hooks_for_platform(db, capsys):
    Hook.objects.create(
        slug="general-start",
        title="General Start",
        event=Hook.Event.SESSION_START,
        platform=Hook.Platform.ANY,
        command="python manage.py check --fail-level ERROR",
    )

    call_command(
        "codex_hooks",
        "list",
        "--event",
        Hook.Event.SESSION_START,
        "--platform",
        Hook.Platform.WINDOWS,
    )
    output = capsys.readouterr().out

    assert json.loads(output)[0]["slug"] == "general-start"
