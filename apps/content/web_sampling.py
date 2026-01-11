from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.content.models import (
    ContentSample,
    WebRequestSampler,
    WebRequestStep,
    WebSample,
    WebSampleAttachment,
)
from apps.content.utils import save_content_sample
from apps.sigils.sigil_context import get_context, set_context
from apps.sigils.sigil_resolver import resolve_sigils

logger = logging.getLogger(__name__)

LOCAL_SIGIL_PATTERN = re.compile(r"\[([^\[\]]+)\]")


@dataclass
class CurlResult:
    status_code: int | None
    headers: dict[str, str]
    body: bytes
    stderr: str
    command: list[str]


def _split_headers(raw: bytes) -> tuple[str, bytes]:
    if not raw:
        return "", b""
    if b"\r\n\r\n" in raw:
        header_bytes, body = raw.rsplit(b"\r\n\r\n", 1)
    elif b"\n\n" in raw:
        header_bytes, body = raw.rsplit(b"\n\n", 1)
    else:
        return "", raw
    return header_bytes.decode("utf-8", errors="replace"), body


def _parse_headers(header_text: str) -> tuple[dict[str, str], int | None]:
    headers: dict[str, str] = {}
    status_code: int | None = None
    for line in header_text.splitlines():
        if line.startswith("HTTP/"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                status_code = int(parts[1])
            continue
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip()] = value.strip()
    return headers, status_code


def _parse_body(body: bytes, headers: dict[str, str]) -> Any:
    content_type = headers.get("Content-Type", "").lower()
    if "json" in content_type:
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return body.decode("utf-8", errors="replace")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")


def _extract_uri_from_command(command: list[str]) -> str:
    for part in command[::-1]:
        if part.startswith("http://") or part.startswith("https://"):
            return part
    return ""


def _resolve_local_sigils(text: str, context: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        parts = token.split(".")
        if not parts:
            return match.group(0)
        key = parts[0]
        if key not in context:
            return match.group(0)
        value: Any = context[key]
        for part in parts[1:]:
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list) and part.isdigit():
                idx = int(part)
                value = value[idx] if 0 <= idx < len(value) else None
            else:
                value = None
            if value is None:
                break
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value)
            except TypeError:
                return match.group(0)
        return str(value)

    return LOCAL_SIGIL_PATTERN.sub(replace, text)


def _run_curl(command: str) -> CurlResult:
    args = shlex.split(command)
    if args and args[0].lower() == "curl":
        args = args[1:]
    full_cmd = ["curl", "-i", "-sS", *args]
    completed = subprocess.run(
        full_cmd,
        capture_output=True,
        check=False,
    )
    header_text, body = _split_headers(completed.stdout)
    headers, status_code = _parse_headers(header_text)
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    return CurlResult(
        status_code=status_code,
        headers=headers,
        body=body,
        stderr=stderr,
        command=full_cmd,
    )


def _store_attachment(
    sampler: WebRequestSampler,
    step: WebRequestStep,
    payload: bytes,
    user,
) -> ContentSample | None:
    if not payload:
        return None
    storage_dir = Path(settings.LOG_DIR) / "web_samples"
    storage_dir.mkdir(parents=True, exist_ok=True)
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
    filename = f"{sampler.slug}-{step.slug}-{timestamp}.bin"
    path = storage_dir / filename
    try:
        path.write_bytes(payload)
    except OSError:
        logger.exception("Unable to write web sample attachment %s", path)
        return None
    return save_content_sample(
        path=path,
        kind=step.attachment_kind,
        method="WEB",
        user=user,
        link_duplicates=True,
        duplicate_log_context=f"web sampler {sampler.pk}",
    )


def _prepare_context_user(user) -> None:
    current = get_context()
    if user is None:
        return
    updated = dict(current)
    User = get_user_model()
    updated[User] = getattr(user, "pk", None)
    set_context(updated)


def execute_sampler(sampler: WebRequestSampler, *, user=None) -> WebSample:
    """Run the sampler's cURL steps and persist the resulting web sample."""

    if sampler is None:
        raise ValueError("Sampler is required")

    base_context = get_context().copy()
    _prepare_context_user(user)
    local_context: dict[str, Any] = {}
    document: dict[str, Any] = {}
    attachments: list[tuple[WebRequestStep, str, ContentSample]] = []
    try:
        for step in sampler.steps.all():
            command_with_context = _resolve_local_sigils(
                step.curl_command, local_context
            )
            resolved_command = resolve_sigils(command_with_context)
            result = _run_curl(resolved_command)
            parsed_body = _parse_body(result.body, result.headers)
            key = f"{step.slug}-{step.order}"
            entry: dict[str, Any] = {
                "slug": step.slug,
                "order": step.order,
                "status": result.status_code,
                "headers": result.headers,
                "command": " ".join(result.command),
                "body": parsed_body,
            }
            if result.stderr:
                entry["stderr"] = result.stderr
            document[key] = entry
            local_context[step.slug] = entry
            local_context[key] = entry
            if step.save_as_content:
                sample = _store_attachment(
                    sampler,
                    step,
                    result.body,
                    user,
                )
                if sample is not None:
                    uri = _extract_uri_from_command(result.command)
                    attachments.append((step, uri, sample))
                    entry["attachment"] = {
                        "uri": uri,
                        "content_sample_id": sample.pk,
                    }
        executed_sample = WebSample.objects.create(
            sampler=sampler,
            executed_by=user,
            document=document,
        )
        for step, uri, content in attachments:
            WebSampleAttachment.objects.create(
                sample=executed_sample,
                step=step,
                uri=uri,
                content_sample=content,
            )
        sampler.last_sampled_at = timezone.now()
        sampler.save(update_fields=["last_sampled_at"])
        return executed_sample
    finally:
        set_context(base_context)


def schedule_pending_samplers(now=None) -> list[int]:
    """Execute any samplers that are due based on their sampling period.

    Returns a list of sampler IDs that were executed.
    """

    now = now or timezone.now()
    executed: list[int] = []
    candidates = WebRequestSampler.objects.filter(
        sampling_period_minutes__isnull=False
    )
    for sampler in candidates:
        period = sampler.sampling_period_minutes or 0
        if period <= 0:
            continue
        threshold = sampler.last_sampled_at or (now - timedelta(minutes=period))
        if threshold + timedelta(minutes=period) > now:
            continue
        runner = sampler.user
        if runner is None and sampler.group_id:
            runner = (
                sampler.group.user_set.filter(is_active=True)
                .order_by("id")
                .first()
            )
        try:
            execute_sampler(sampler, user=runner)
            executed.append(sampler.pk)
        except Exception:
            logger.exception("Web sampler %s failed", sampler.pk)
    return executed
