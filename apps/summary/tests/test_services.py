from __future__ import annotations

import pytest
from django.test import override_settings

from apps.summary.models import LLMSummaryConfig
from apps.summary.services import (
    build_summary_runtime_launch_plan,
    get_summary_config,
    probe_summary_runtime,
    sync_summary_runtime_service_lock,
    summary_runtime_is_ready,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


@pytest.mark.django_db
def test_probe_summary_runtime_marks_ready_when_runtime_serves_one_model(monkeypatch):
    config = get_summary_config()
    config.backend = LLMSummaryConfig.Backend.LLAMA_CPP_SERVER
    config.selected_model = "gemma-4-e2b-it"
    config.runtime_base_url = "http://127.0.0.1:8080/v1"
    config.is_active = True
    config.save(
        update_fields=[
            "backend",
            "selected_model",
            "runtime_base_url",
            "is_active",
            "updated_at",
        ]
    )

    monkeypatch.setattr(
        "apps.summary.services.requests.get",
        lambda url, timeout: _FakeResponse(
            {"data": [{"id": "ggml-org/gemma-4-E2B-it-GGUF"}]}
        ),
    )

    state = probe_summary_runtime(config)

    config.refresh_from_db()
    assert state.ready is True
    assert config.runtime_is_ready is True
    assert config.runtime_model_id == "ggml-org/gemma-4-E2B-it-GGUF"
    assert summary_runtime_is_ready(config) is True


@pytest.mark.django_db
def test_probe_summary_runtime_requires_selected_model():
    config = get_summary_config()
    config.backend = LLMSummaryConfig.Backend.LLAMA_CPP_SERVER
    config.selected_model = ""
    config.runtime_is_ready = True
    config.save(
        update_fields=[
            "backend",
            "selected_model",
            "runtime_is_ready",
            "updated_at",
        ]
    )

    state = probe_summary_runtime(config)

    config.refresh_from_db()
    assert state.ready is False
    assert config.runtime_is_ready is False
    assert "No summary model is selected" in config.last_runtime_error


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_build_summary_runtime_launch_plan_uses_selected_model_and_binary(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    config = get_summary_config()
    config.backend = LLMSummaryConfig.Backend.LLAMA_CPP_SERVER
    config.selected_model = "gemma-4-e2b-it"
    config.model_path = str(tmp_path / "work" / "llm" / "lcd-summary")
    config.runtime_base_url = "http://127.0.0.1:9090/v1"
    config.runtime_binary_path = "/usr/local/bin/llama-server"
    config.is_active = True
    config.save(
        update_fields=[
            "backend",
            "selected_model",
            "model_path",
            "runtime_base_url",
            "runtime_binary_path",
            "is_active",
            "updated_at",
        ]
    )

    plan = build_summary_runtime_launch_plan(config)

    assert plan.command == (
        "/usr/local/bin/llama-server",
        "-hf",
        "ggml-org/gemma-4-E2B-it-GGUF",
        "--host",
        "127.0.0.1",
        "--port",
        "9090",
    )
    assert plan.env["HF_HOME"] == str(tmp_path / "work" / "llm" / "lcd-summary")
    assert "ggml-org/gemma-4-E2B-it-GGUF" in plan.audit_command


@pytest.mark.django_db
@override_settings(BASE_DIR="/tmp")
def test_sync_summary_runtime_service_lock_tracks_selected_model(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    config = get_summary_config()
    config.backend = LLMSummaryConfig.Backend.LLAMA_CPP_SERVER
    config.selected_model = "gemma-4-e2b-it"
    config.model_path = str(tmp_path / "work" / "llm" / "lcd-summary")
    config.runtime_base_url = "http://127.0.0.1:8080/v1"
    config.runtime_binary_path = "llama-server"
    config.is_active = True
    config.save(
        update_fields=[
            "backend",
            "selected_model",
            "model_path",
            "runtime_base_url",
            "runtime_binary_path",
            "is_active",
            "updated_at",
        ]
    )

    enabled = sync_summary_runtime_service_lock(config, base_dir=tmp_path)

    config.refresh_from_db()
    assert enabled is True
    assert (tmp_path / ".locks" / "summary-runtime-service.lck").exists()
    assert "llama-server -hf ggml-org/gemma-4-E2B-it-GGUF" in config.model_command_audit

    config.selected_model = ""
    config.save(update_fields=["selected_model", "updated_at"])

    enabled = sync_summary_runtime_service_lock(config, base_dir=tmp_path)

    assert enabled is False
    assert not (tmp_path / ".locks" / "summary-runtime-service.lck").exists()
