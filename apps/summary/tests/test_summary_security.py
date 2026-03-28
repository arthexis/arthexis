from __future__ import annotations

import pytest

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
