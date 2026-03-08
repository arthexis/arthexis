"""Tests for the upgrade management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.management.commands import upgrade as command_module
from apps.nodes.models import Node, NodeUpgradePolicyAssignment, UpgradePolicy


@pytest.mark.django_db
def test_upgrade_show_outputs_summary(monkeypatch):
    """Show action should print a compact upgrade summary."""

    monkeypatch.setattr(
        command_module,
        "_build_auto_upgrade_report",
        lambda limit=15: {
            "summary": {
                "state": "warning",
                "headline": "Needs attention",
                "issues": [{"severity": "warning", "label": "Task disabled"}],
            },
            "settings": {
                "channels": ["stable"],
                "skip_revisions": ["abc123"],
            },
            "schedule": {"next_run": "Soon", "last_run_at": "Yesterday", "failure_count": 2},
            "log_entries": [{"timestamp": "Now", "message": "Checked"}],
        },
    )

    out = StringIO()
    call_command("upgrade", "show", stdout=out)

    output = out.getvalue()
    assert "Status: warning" in output
    assert "Task disabled" in output
    assert "abc123" in output


@pytest.mark.django_db
def test_upgrade_check_uses_channel_override(monkeypatch, settings, tmp_path):
    """Check action should normalize requested channels and trigger checks."""

    settings.BASE_DIR = tmp_path
    seen: dict[str, object] = {}

    def _record_trigger(*, channel_override=None):
        seen["channel_override"] = channel_override
        return True

    monkeypatch.setattr(command_module, "_trigger_upgrade_check", _record_trigger)

    out = StringIO()
    call_command("upgrade", "check", "--channel", "latest", stdout=out)

    assert seen["channel_override"] == "latest"
    assert "queued" in out.getvalue().lower()


@pytest.mark.django_db
def test_upgrade_check_treats_stable_alias_as_no_override(monkeypatch, settings, tmp_path):
    """Stable aliases should not force manual override semantics."""

    settings.BASE_DIR = tmp_path
    seen: dict[str, object] = {}

    def _record_trigger(*, channel_override=None):
        seen["channel_override"] = channel_override
        return True

    monkeypatch.setattr(command_module, "_trigger_upgrade_check", _record_trigger)

    call_command("upgrade", "check", "--channel", "normal")

    assert seen["channel_override"] is None


@pytest.mark.django_db
def test_upgrade_channel_updates_assigned_policy_channel(monkeypatch):
    """Channel action should update local-node assigned upgrade policies."""

    node = Node.objects.create(hostname="local-node", current_relation=Node.Relation.SELF)
    policy = UpgradePolicy.objects.create(name="Stable", channel=UpgradePolicy.Channel.STABLE)
    NodeUpgradePolicyAssignment.objects.create(node=node, policy=policy)

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    call_command("upgrade", "channel", "unstable")

    policy.refresh_from_db()
    assert policy.channel == UpgradePolicy.Channel.UNSTABLE


@pytest.mark.django_db
def test_upgrade_channel_rejects_unknown_channel():
    """Channel action should fail fast for unsupported channel values."""

    with pytest.raises(CommandError):
        call_command("upgrade", "channel", "invalid")
