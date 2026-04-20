from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command


def test_summary_command_lists_built_in_models():
    stdout = StringIO()

    call_command("summary", "--list-models", stdout=stdout)

    output = stdout.getvalue()
    assert "gemma-4-e2b-it" in output
    assert "ggml-org/gemma-4-E2B-it-GGUF" in output


@pytest.mark.django_db
def test_summary_command_prints_runtime_command():
    stdout = StringIO()

    call_command(
        "summary",
        "--select-model",
        "gemma-4-e2b-it",
        "--runtime-base-url",
        "http://127.0.0.1:8080/v1",
        "--runtime-binary-path",
        "/opt/llama-server",
        "--print-runtime-command",
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert "/opt/llama-server -hf ggml-org/gemma-4-E2B-it-GGUF --host 127.0.0.1 --port 8080" in output
