from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.nodes.models import Node, NodeRole
from apps.summary.models import LLMSummaryConfig
from apps.summary.services import get_summary_config


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


@pytest.mark.django_db
def test_summary_status_reports_file_output_target(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()
    role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(
        hostname="control",
        public_endpoint="control",
        role=role,
    )
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)
    LLMSummaryConfig.objects.create(
        output_target=LLMSummaryConfig.OutputTarget.FILE,
        output_file_path="logs/summary/latest.txt",
        output_file_format=LLMSummaryConfig.OutputFileFormat.BOTH,
        last_output_file_path=str(tmp_path / "logs" / "summary" / "latest.txt"),
    )
    stdout = StringIO()

    call_command("summary", stdout=stdout)

    output = stdout.getvalue()
    assert "Output target: file" in output
    assert (
        f"Configured file path: {tmp_path / 'logs' / 'summary' / 'latest.txt'}"
        in output
    )
    assert f"Last output file: {tmp_path / 'logs' / 'summary' / 'latest.txt'}" in output
    assert "Celery lock:" in output


@pytest.mark.django_db
def test_summary_status_reports_invalid_file_output_path(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()
    role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(
        hostname="control",
        public_endpoint="control",
        role=role,
    )
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)
    LLMSummaryConfig.objects.create(
        output_target=LLMSummaryConfig.OutputTarget.FILE,
        output_file_path="../outside.txt",
        output_file_format=LLMSummaryConfig.OutputFileFormat.TEXT,
    )
    stdout = StringIO()

    call_command("summary", stdout=stdout)

    output = stdout.getvalue()
    assert "Output target: file" in output
    assert "Configured file path: invalid (" in output
    assert "cannot contain '..'" in output


@pytest.mark.django_db
def test_summary_status_reports_deterministic_mode(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    Node._local_cache.clear()
    role = NodeRole.objects.create(name="Control")
    node = Node.objects.create(
        hostname="control",
        public_endpoint="control",
        role=role,
    )
    Node.objects.filter(pk=node.pk).update(current_relation=Node.Relation.SELF)
    LLMSummaryConfig.objects.create()
    stdout = StringIO()

    call_command("summary", stdout=stdout)

    output = stdout.getvalue()
    assert "Mode: Deterministic built-in summarizer" in output
    assert "Ollama" not in output


@pytest.mark.django_db
def test_summary_config_reconciles_legacy_slug():
    legacy = LLMSummaryConfig.objects.create(
        slug="lcd-log-summary",
        display="LCD Log Summary",
    )

    config = get_summary_config()

    assert config.pk == legacy.pk
    assert config.slug == "log-summary"
    assert config.display == "Log Summary"
