from datetime import timedelta

import pytest
from django.utils import timezone

from apps.content import web_sampling
from apps.content.models import (
    ContentSample,
    WebRequestSampler,
    WebRequestStep,
    WebSample,
    WebSampleAttachment,
)
from apps.content.web_sampling import execute_sampler, schedule_pending_samplers


class DummyCompletedProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = 0


def _install_fake_curl(monkeypatch, responses):
    calls = []

    def fake_run(cmd, capture_output=False, check=False):
        calls.append(cmd)
        return responses[len(calls) - 1]

    monkeypatch.setattr(web_sampling.subprocess, "run", fake_run)
    return calls


@pytest.mark.django_db
def test_execute_sampler_with_local_context(monkeypatch, django_user_model):
    user = django_user_model.objects.create(username="curator")
    sampler = WebRequestSampler.objects.create(slug="sample", label="Sample")
    WebRequestStep.objects.create(
        sampler=sampler,
        order=1,
        slug="first",
        curl_command="https://example.com/api",
    )
    WebRequestStep.objects.create(
        sampler=sampler,
        order=2,
        slug="second",
        curl_command="https://example.com/next?token=[first.body.token]",
    )

    responses = [
        DummyCompletedProcess(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"token\": \"abc\", \"body\": {\"token\": \"abc\"}}"
        ),
        DummyCompletedProcess(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\": true}"
        ),
    ]
    calls = _install_fake_curl(monkeypatch, responses)

    sample = execute_sampler(sampler, user=user)
    assert isinstance(sample, WebSample)

    assert "token=abc" in " ".join(" ".join(call) for call in calls[1:])
    document = sample.document
    assert document["first-1"]["body"]["token"] == "abc"
    assert document["second-2"]["body"]["ok"] is True


@pytest.mark.django_db
def test_execute_sampler_stores_attachment(monkeypatch, settings, tmp_path, django_user_model):
    settings.LOG_DIR = tmp_path
    user = django_user_model.objects.create(username="owner")
    sampler = WebRequestSampler.objects.create(slug="attachments", label="Attachments")
    step = WebRequestStep.objects.create(
        sampler=sampler,
        order=1,
        slug="page",
        curl_command="https://example.com/file.txt",
        save_as_content=True,
        attachment_kind=ContentSample.TEXT,
    )

    responses = [
        DummyCompletedProcess(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nhello world"
        )
    ]
    _install_fake_curl(monkeypatch, responses)

    sample = execute_sampler(sampler, user=user)
    attachment = WebSampleAttachment.objects.get(sample=sample)

    assert attachment.content_sample.kind == ContentSample.TEXT
    assert attachment.uri.startswith("https://example.com")
    key = f"{step.slug}-{step.order}"
    assert sample.document[key]["attachment"]["content_sample_id"] == attachment.content_sample_id


@pytest.mark.django_db
def test_schedule_pending_samplers(monkeypatch):
    sampler = WebRequestSampler.objects.create(
        slug="schedule",
        label="Schedule",
        sampling_period_minutes=1,
    )
    executed = []

    def fake_execute(target, user=None):
        executed.append(target.pk)
        target.last_sampled_at = timezone.now()
        target.save(update_fields=["last_sampled_at"])

    monkeypatch.setattr(web_sampling, "execute_sampler", fake_execute)

    now = timezone.now()
    ran = schedule_pending_samplers(now=now)
    assert executed == [sampler.pk]
    assert ran == [sampler.pk]

    later = now + timedelta(seconds=30)
    rerun = schedule_pending_samplers(now=later)
    assert rerun == []
    latest = now + timedelta(minutes=2)
    again = schedule_pending_samplers(now=latest)
    assert again == [sampler.pk]
