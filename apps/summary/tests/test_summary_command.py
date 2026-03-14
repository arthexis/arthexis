from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.features.models import Feature
from apps.nodes.models import Node
from apps.screens.startup_notifications import (
    LCD_CHANNELS_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    LCD_RUNTIME_LOCK_FILE,
)
from apps.summary.constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from apps.summary.services import get_summary_config


@pytest.fixture
def llm_summary_suite_feature_enabled() -> Feature:
    """Enable the suite gate used by summary automation tests."""

    feature, _ = Feature.objects.update_or_create(
        slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
        defaults={
            "display": "LLM Summary Suite",
            "source": Feature.Source.CUSTOM,
            "is_enabled": True,
        },
    )
    return feature




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
    assert (tmp_path / ".locks" / LCD_RUNTIME_LOCK_FILE).exists()
    assert node.features.filter(slug="llm-summary").exists()
    assert node.features.filter(slug="celery-queue").exists()
    assert node.features.filter(slug="lcd-screen").exists()











@pytest.mark.django_db
def test_summary_command_run_now_executes_when_suite_feature_enabled(
    tmp_path: Path, llm_summary_suite_feature_enabled: Feature
) -> None:
    """Regression: enabling suite automation gate should restore run-now execution."""

    Node.objects.create(hostname="local", current_relation=Node.Relation.SELF)

    out = StringIO()
    with (
        override_settings(BASE_DIR=tmp_path),
        patch(
            "apps.summary.management.commands.summary.Command._run_summary_task_now",
            return_value="wrote:2",
        ) as run_now,
    ):
        call_command("summary", "--run-now", stdout=out)

    output = out.getvalue()
    assert "Run now: wrote:2" in output
    run_now.assert_called_once_with()
