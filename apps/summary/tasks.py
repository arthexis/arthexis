from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path
import subprocess
import time

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.nodes.models import Node
from apps.screens.startup_notifications import LCD_LOW_LOCK_FILE

from .services import (
    build_summary_prompt,
    collect_recent_logs,
    compact_log_chunks,
    ensure_local_model,
    get_summary_config,
    normalize_screens,
    parse_screens,
    render_lcd_payload,
)

logger = logging.getLogger(__name__)

DEFAULT_SLEEP_SECONDS = 30
DEFAULT_PROMPT_TIMEOUT = 240


class LocalLLMSummarizer:
    def __init__(
        self, *, command: str | None = None, timeout: int = DEFAULT_PROMPT_TIMEOUT
    ):
        self.command = command
        self.timeout = timeout

    def summarize(self, prompt: str) -> str:
        if not self.command:
            return self._fallback(prompt)
        try:
            result = subprocess.run(
                self.command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                shell=isinstance(self.command, str),
            )
        except Exception:
            logger.exception("Failed to run local LLM command")
            return self._fallback(prompt)
        if result.returncode != 0:
            logger.warning("Local LLM command returned %s", result.returncode)
            return self._fallback(prompt)
        return result.stdout.strip()

    def _fallback(self, prompt: str) -> str:
        log_lines: list[str] = []
        in_logs = False
        for line in prompt.splitlines():
            if line.strip() == "LOGS:":
                in_logs = True
                continue
            if in_logs and line.strip():
                log_lines.append(line)
        sample = log_lines[-20:] if log_lines else [line for line in prompt.splitlines() if line]
        summary = []
        for idx in range(0, min(len(sample), 20), 2):
            subject = f"LOG {idx // 2 + 1}"
            body = sample[idx][:16]
            summary.append(subject)
            summary.append(body)
            summary.append("---")
        return "\n".join(summary)


def _write_lcd_frames(
    frames: list[tuple[str, str]],
    *,
    lock_file: Path,
    sleep_seconds: int = DEFAULT_SLEEP_SECONDS,
    sleep_fn=time.sleep,
) -> None:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    for subject, body in frames:
        payload = render_lcd_payload(subject, body)
        lock_file.write_text(payload, encoding="utf-8")
        sleep_fn(sleep_seconds)


@shared_task(name="summary.tasks.generate_lcd_log_summary")
def generate_lcd_log_summary() -> str:
    node = Node.get_local()
    if not node:
        return "skipped:no-node"

    if not node.has_feature("llm-summary"):
        return "skipped:feature-disabled"

    config = get_summary_config()
    if not config.is_active:
        return "skipped:inactive"

    ensure_local_model(config)

    now = timezone.now()
    since = config.last_run_at or (now - timedelta(minutes=5))
    chunks = collect_recent_logs(config, since=since)
    compacted_logs = compact_log_chunks(chunks)
    if not compacted_logs:
        config.last_run_at = now
        config.save(update_fields=["last_run_at", "log_offsets", "model_path", "installed_at", "updated_at"])
        return "skipped:no-logs"

    prompt = build_summary_prompt(compacted_logs, now=now)
    command = config.model_command or getattr(settings, "LLM_SUMMARY_COMMAND", "") or None
    summarizer = LocalLLMSummarizer(
        command=command,
        timeout=getattr(settings, "LLM_SUMMARY_TIMEOUT", DEFAULT_PROMPT_TIMEOUT),
    )
    output = summarizer.summarize(prompt)
    screens = normalize_screens(parse_screens(output))

    if not screens:
        screens = normalize_screens([("No events", "-"), ("Chk logs", "manual")])

    lock_file = Path(settings.BASE_DIR) / ".locks" / LCD_LOW_LOCK_FILE
    _write_lcd_frames(screens[:10], lock_file=lock_file)

    config.last_run_at = now
    config.last_prompt = prompt
    config.last_output = output
    config.save(
        update_fields=[
            "last_run_at",
            "last_prompt",
            "last_output",
            "log_offsets",
            "model_path",
            "installed_at",
            "updated_at",
        ]
    )
    return f"wrote:{len(screens)}"
