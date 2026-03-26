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
def test_model_command_is_removed_and_audit_field_is_present() -> None:
    """Verify the executable command field is gone while audit metadata remains available."""
    config = LLMSummaryConfig.objects.create(
        slug="audit-check",
        display="Audit Check",
        model_path="/tmp/model",
        backend=LLMSummaryConfig.Backend.DETERMINISTIC,
        model_command_audit="legacy command text",
    )

    assert config.model_command_audit == "legacy command text"

    with pytest.raises(AttributeError):
        _ = config.model_command
