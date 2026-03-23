from __future__ import annotations

from pathlib import Path

import pytest

from apps.features.models import Feature
from apps.summary.constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from apps.summary.models import LLMSummaryConfig
from apps.summary.services import execute_log_summary_generation, get_summary_config
from apps.tasks.tasks import LocalLLMSummarizer


@pytest.mark.django_db
def test_local_llm_summarizer_never_uses_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("subprocess should not run")
        ),
    )

    summarizer = LocalLLMSummarizer()

    output = summarizer.summarize("LOGS:\n[app.log]\nERR gateway offline\n")

    assert "LOG 1" in output
    assert "ERR gateway offl" in output


@pytest.mark.django_db
def test_execute_log_summary_generation_ignores_legacy_feature_command(
    monkeypatch: pytest.MonkeyPatch,
    settings,
    tmp_path: Path,
) -> None:
    settings.BASE_DIR = tmp_path
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "system.log").write_text(
        "2026-03-23 12:00:00 INFO charger rebooted\n",
        encoding="utf-8",
    )
    settings.LOG_DIR = log_dir

    feature = Feature.objects.create(
        slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
        display="LLM Summary Suite",
        source=Feature.Source.CUSTOM,
        is_enabled=True,
        metadata={
            "parameters": {
                "backend": "deterministic",
                "model_command": "python -c 'raise SystemExit(99)'",
                "timeout_seconds": "1",
            }
        },
    )
    assert feature.pk is not None

    config = get_summary_config()
    config.is_active = True
    config.last_run_at = None
    config.log_offsets = {}
    config.save(update_fields=["is_active", "last_run_at", "log_offsets", "updated_at"])

    class FakeNode:
        """Minimal node stub exposing the llm-summary feature flag."""

        def has_feature(self, slug: str) -> bool:
            """Return whether the requested feature is enabled for the fake node."""

            return slug == "llm-summary"

    monkeypatch.setattr("apps.nodes.models.Node.get_local", lambda: FakeNode())
    monkeypatch.setattr(
        "apps.summary.services.is_suite_feature_enabled",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "apps.summary.services.ensure_local_model",
        lambda config, preferred_path=None: tmp_path / "work",
    )
    monkeypatch.setattr(
        "apps.tasks.tasks._write_lcd_frames",
        lambda frames, lock_file: None,
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("subprocess should not run")
        ),
    )

    status = execute_log_summary_generation()

    config.refresh_from_db()
    assert status == "wrote:10"
    assert config.last_output
    assert "INF charger rebo" in config.last_output


@pytest.mark.django_db
def test_legacy_model_command_text_is_retained_only_as_audit_metadata() -> None:
    config = LLMSummaryConfig.objects.create(
        slug="audit-check",
        display="Audit Check",
        model_path="/tmp/model",
        backend=LLMSummaryConfig.Backend.DETERMINISTIC,
        model_command_audit="legacy command text",
    )

    assert config.model_command_audit == "legacy command text"
