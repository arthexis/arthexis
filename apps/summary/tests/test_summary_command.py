from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.nodes.models import Node
from apps.summary.services import get_summary_config


@pytest.mark.django_db
def test_summary_command_prints_status_and_plan(tmp_path: Path) -> None:
    """The summary command should report status and parsed screen plan."""

    node = Node.objects.create(hostname="local", current_relation=Node.Relation.SELF)
    config = get_summary_config()
    config.last_output = """SCREEN 1:
ALARM
CHECK PUMP
---
SCREEN 2:
TEMP OK
HOLD
"""
    config.save(update_fields=["last_output", "updated_at"])

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "lcd-low").write_text("TEMP OK\nHOLD\n", encoding="utf-8")
    (lock_dir / "lcd-channels.lck").write_text("high, low, stats\n", encoding="utf-8")

    out = StringIO()
    with override_settings(BASE_DIR=tmp_path):
        call_command("summary", stdout=out)

    output = out.getvalue()
    assert f"Node: {node.hostname}" in output
    assert "Summary Plan" in output
    assert "  01. ALARM | CHECK PUMP" in output
    assert "* 02. TEMP OK | HOLD" in output
    assert "Channel order: high, low, stats" in output


@pytest.mark.django_db
def test_summary_command_enabled_turns_on_prereqs(tmp_path: Path) -> None:
    """The --enabled flag should activate config, locks, and key features."""

    node = Node.objects.create(hostname="local", current_relation=Node.Relation.SELF)

    out = StringIO()
    with override_settings(BASE_DIR=tmp_path):
        call_command("summary", "--enabled", stdout=out)

    node.refresh_from_db()
    config = get_summary_config()

    assert config.is_active is True
    assert config.model_path
    assert (tmp_path / ".locks" / "celery.lck").exists()
    assert (tmp_path / ".locks" / "lcd_screen.lck").exists()
    assert node.features.filter(slug="llm-summary").exists()
    assert node.features.filter(slug="celery-queue").exists()
    assert node.features.filter(slug="lcd-screen").exists()
