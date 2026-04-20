from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import textwrap
from typing import Iterable
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone
import requests

from apps.features.parameters import get_feature_parameter
from apps.features.utils import is_suite_feature_enabled
from apps.screens.startup_notifications import render_lcd_lock_file

from .catalog import SummaryModelSpec, get_summary_model_spec
from .constants import LLM_SUMMARY_RUNTIME_SERVICE_LOCK, LLM_SUMMARY_SUITE_FEATURE_SLUG
from .models import LLMSummaryConfig

logger = logging.getLogger(__name__)

LCD_COLUMNS = 16
LCD_SUMMARY_FRAME_COUNT = 10
DEFAULT_MODEL_DIR = Path(settings.BASE_DIR) / "work" / "llm" / "lcd-summary"
DEFAULT_MODEL_FILE = "MODEL.README"
DEFAULT_RUNTIME_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_RUNTIME_SERVER_BINARY = "llama-server"
LOCAL_RUNTIME_HOSTS = frozenset({"127.0.0.1", "0.0.0.0", "::1", "localhost"})

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+)?\s+"
)
LEVEL_RE = re.compile(r"\b(INFO|DEBUG|WARNING|ERROR|CRITICAL)\b")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class LogChunk:
    path: Path
    content: str


@dataclass(frozen=True, slots=True)
class SummaryRuntimeState:
    """Current model/runtime binding state for the LCD summarizer."""

    ready: bool
    detail: str
    selected_model: SummaryModelSpec | None = None
    runtime_model_id: str = ""
    runtime_base_url: str = ""


@dataclass(frozen=True, slots=True)
class SummaryRuntimeLaunchPlan:
    """Command plan for the managed local llama.cpp summary runtime."""

    command: tuple[str, ...]
    env: dict[str, str]
    audit_command: str
    runtime_base_url: str
    selected_model: SummaryModelSpec


def get_summary_config() -> LLMSummaryConfig:
    """Return the singleton LCD summary configuration record."""

    config, _created = LLMSummaryConfig.objects.get_or_create(
        slug="lcd-log-summary",
        defaults={"display": "LCD Log Summary"},
    )
    return config


def _resolve_selected_model_slug(config: LLMSummaryConfig) -> str:
    if config.selected_model:
        return str(config.selected_model).strip()
    selected_model = get_feature_parameter(
        LLM_SUMMARY_SUITE_FEATURE_SLUG,
        "selected_model",
        fallback="",
    )
    if selected_model:
        return selected_model
    return ""


def _resolve_model_path(config: LLMSummaryConfig) -> Path:
    suite_model_path = get_feature_parameter(
        LLM_SUMMARY_SUITE_FEATURE_SLUG,
        "model_path",
        fallback="",
    )
    if suite_model_path:
        return Path(suite_model_path)
    if config.model_path:
        return Path(config.model_path)
    env_override = os.getenv("ARTHEXIS_LLM_SUMMARY_MODEL")
    if env_override:
        return Path(env_override)
    return DEFAULT_MODEL_DIR


def _resolve_runtime_base_url(config: LLMSummaryConfig) -> str:
    runtime_base_url = (config.runtime_base_url or "").strip()
    if not runtime_base_url:
        runtime_base_url = get_feature_parameter(
            LLM_SUMMARY_SUITE_FEATURE_SLUG,
            "runtime_base_url",
            fallback="",
        )
    if not runtime_base_url:
        runtime_base_url = os.getenv("ARTHEXIS_LLM_SUMMARY_BASE_URL", "").strip()
    if not runtime_base_url:
        runtime_base_url = DEFAULT_RUNTIME_BASE_URL
    runtime_base_url = runtime_base_url.rstrip("/")
    if runtime_base_url.endswith("/v1"):
        return runtime_base_url
    return f"{runtime_base_url}/v1"


def _resolve_runtime_binary_path(config: LLMSummaryConfig) -> str:
    runtime_binary_path = (config.runtime_binary_path or "").strip()
    if not runtime_binary_path:
        runtime_binary_path = get_feature_parameter(
            LLM_SUMMARY_SUITE_FEATURE_SLUG,
            "runtime_binary_path",
            fallback="",
        )
    if not runtime_binary_path:
        runtime_binary_path = os.getenv("ARTHEXIS_LLM_SUMMARY_SERVER_BIN", "").strip()
    if not runtime_binary_path:
        runtime_binary_path = DEFAULT_RUNTIME_SERVER_BINARY
    return runtime_binary_path


def resolve_runtime_base_url(config: LLMSummaryConfig) -> str:
    """Return the effective base URL for the local summary runtime."""

    return _resolve_runtime_base_url(config)


def resolve_runtime_binary_path(config: LLMSummaryConfig) -> str:
    """Return the effective binary used to launch the local runtime service."""

    return _resolve_runtime_binary_path(config)


def resolve_runtime_model_id(config: LLMSummaryConfig) -> str:
    """Return the resolved model identifier exposed by the runtime."""

    if config.runtime_model_id:
        return str(config.runtime_model_id).strip()
    runtime_model_id = get_feature_parameter(
        LLM_SUMMARY_SUITE_FEATURE_SLUG,
        "runtime_model_id",
        fallback="",
    )
    if runtime_model_id:
        return runtime_model_id
    return ""


def resolve_model_path(config: LLMSummaryConfig) -> Path:
    """Return the effective local model directory for ``config``."""

    return _resolve_model_path(config)


def get_selected_summary_model(config: LLMSummaryConfig) -> SummaryModelSpec | None:
    """Return the selected built-in model profile, if any."""

    return get_summary_model_spec(_resolve_selected_model_slug(config))


def _candidate_runtime_model_ids(spec: SummaryModelSpec) -> tuple[str, ...]:
    repo = spec.hf_repo
    repo_dash = repo.replace("/", "-")
    return (
        repo,
        repo.lower(),
        repo_dash,
        repo_dash.lower(),
        spec.slug,
        spec.slug.replace("_", "-"),
    )


def _resolve_runtime_endpoint(config: LLMSummaryConfig) -> tuple[str, str, int]:
    runtime_base_url = _resolve_runtime_base_url(config)
    parsed = urlparse(runtime_base_url)
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        raise ValueError("Runtime base URL must include a host.")
    if hostname not in LOCAL_RUNTIME_HOSTS:
        raise ValueError(
            "Runtime base URL must target the local node for managed summary runtime service."
        )
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return runtime_base_url, hostname, port


def build_summary_runtime_launch_plan(
    config: LLMSummaryConfig,
) -> SummaryRuntimeLaunchPlan:
    """Return the managed llama.cpp launch command for the selected summary model."""

    if not config.is_active:
        raise ValueError("Summary config is inactive.")
    if config.backend != LLMSummaryConfig.Backend.LLAMA_CPP_SERVER:
        raise ValueError("Summary backend must be llama.cpp to manage the local runtime.")

    selected_model = get_selected_summary_model(config)
    if selected_model is None:
        raise ValueError("Select a summary model before managing the runtime service.")

    runtime_base_url, hostname, port = _resolve_runtime_endpoint(config)
    runtime_binary_path = _resolve_runtime_binary_path(config)
    model_cache_dir = resolve_model_path(config)
    env = {
        "HF_HOME": str(model_cache_dir),
        "HF_HUB_CACHE": str(model_cache_dir),
    }
    command = (
        runtime_binary_path,
        "-hf",
        selected_model.hf_repo,
        "--host",
        hostname,
        "--port",
        str(port),
    )
    env_prefix = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in sorted(env.items())
    )
    command_text = " ".join(shlex.quote(part) for part in command)
    audit_command = f"{env_prefix} {command_text}".strip()
    return SummaryRuntimeLaunchPlan(
        command=command,
        env=env,
        audit_command=audit_command,
        runtime_base_url=runtime_base_url,
        selected_model=selected_model,
    )


def summary_runtime_service_lock_path(*, base_dir: Path | None = None) -> Path:
    """Return the lock file that enables the managed summary runtime service."""

    resolved_base_dir = Path(base_dir or settings.BASE_DIR)
    return resolved_base_dir / ".locks" / LLM_SUMMARY_RUNTIME_SERVICE_LOCK


def summary_runtime_service_lock_enabled(*, base_dir: Path | None = None) -> bool:
    """Return whether the managed summary runtime lock is currently present."""

    try:
        return summary_runtime_service_lock_path(base_dir=base_dir).exists()
    except OSError:
        return False


def sync_summary_runtime_service_lock(
    config: LLMSummaryConfig,
    *,
    base_dir: Path | None = None,
) -> bool:
    """Create or remove the managed runtime lock based on current summary settings."""

    lock_path = summary_runtime_service_lock_path(base_dir=base_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    audit_value = ""
    enabled = False
    try:
        launch_plan = build_summary_runtime_launch_plan(config)
    except ValueError as exc:
        audit_value = str(exc)
    else:
        enabled = True
        audit_value = launch_plan.audit_command

    try:
        if enabled:
            lock_path.touch(exist_ok=True)
        elif lock_path.exists():
            lock_path.unlink()
    except OSError:
        logger.warning("Unable to reconcile summary runtime lock %s", lock_path, exc_info=True)

    if config.pk and config.model_command_audit != audit_value:
        config.model_command_audit = audit_value
        config.save(update_fields=["model_command_audit", "updated_at"])
    return enabled


def launch_summary_runtime_server(config: LLMSummaryConfig) -> None:
    """Replace the current process with the configured llama.cpp server."""

    launch_plan = build_summary_runtime_launch_plan(config)
    cache_dir = Path(launch_plan.env["HF_HOME"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    binary = launch_plan.command[0]
    resolved_binary = binary
    if os.path.sep not in binary:
        resolved_binary = shutil.which(binary) or ""
    if not resolved_binary:
        raise RuntimeError(
            f"Unable to find the summary runtime binary '{binary}'."
        )

    if config.model_command_audit != launch_plan.audit_command:
        config.model_command_audit = launch_plan.audit_command
        config.save(update_fields=["model_command_audit", "updated_at"])

    env = os.environ.copy()
    env.update(launch_plan.env)
    os.execvpe(
        resolved_binary,
        [resolved_binary, *launch_plan.command[1:]],
        env,
    )


def probe_summary_runtime(
    config: LLMSummaryConfig,
) -> SummaryRuntimeState:
    """Probe the configured runtime and persist readiness state."""

    selected_model = get_selected_summary_model(config)
    now = timezone.now()

    if not config.is_active:
        detail = "Summary config is inactive."
        config.runtime_is_ready = False
        config.runtime_model_id = ""
        config.last_runtime_error = detail
        config.last_runtime_check_at = now
        config.save(
            update_fields=[
                "runtime_is_ready",
                "runtime_model_id",
                "last_runtime_error",
                "last_runtime_check_at",
                "updated_at",
            ]
        )
        return SummaryRuntimeState(False, detail)

    if config.backend != LLMSummaryConfig.Backend.LLAMA_CPP_SERVER:
        detail = "Configured backend is not model-backed."
        config.runtime_is_ready = False
        config.runtime_model_id = ""
        config.last_runtime_error = detail
        config.last_runtime_check_at = now
        config.save(
            update_fields=[
                "runtime_is_ready",
                "runtime_model_id",
                "last_runtime_error",
                "last_runtime_check_at",
                "updated_at",
            ]
        )
        return SummaryRuntimeState(False, detail)

    if selected_model is None:
        detail = "No summary model is selected."
        config.runtime_is_ready = False
        config.runtime_model_id = ""
        config.last_runtime_error = detail
        config.last_runtime_check_at = now
        config.save(
            update_fields=[
                "runtime_is_ready",
                "runtime_model_id",
                "last_runtime_error",
                "last_runtime_check_at",
                "updated_at",
            ]
        )
        return SummaryRuntimeState(False, detail)

    runtime_base_url = _resolve_runtime_base_url(config)
    try:
        response = requests.get(f"{runtime_base_url}/models", timeout=5)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        runtime_ids = [
            str(item.get("id", "")).strip()
            for item in data
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ]
        if not runtime_ids:
            raise RuntimeError("Runtime returned no models from /models.")

        configured_id = (config.runtime_model_id or "").strip()
        if configured_id and configured_id in runtime_ids:
            runtime_model_id = configured_id
        elif len(runtime_ids) == 1:
            runtime_model_id = runtime_ids[0]
        else:
            runtime_model_id = next(
                (
                    candidate
                    for candidate in runtime_ids
                    if candidate in _candidate_runtime_model_ids(selected_model)
                ),
                "",
            )
            if not runtime_model_id:
                raise RuntimeError(
                    "Runtime exposes multiple models and none matches the selected catalog entry."
                )
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        detail = f"{selected_model.display}: {exc}"
        config.runtime_is_ready = False
        config.runtime_model_id = ""
        config.last_runtime_error = detail
        config.last_runtime_check_at = now
        config.save(
            update_fields=[
                "runtime_is_ready",
                "runtime_model_id",
                "last_runtime_error",
                "last_runtime_check_at",
                "updated_at",
            ]
        )
        return SummaryRuntimeState(
            False,
            detail,
            selected_model=selected_model,
            runtime_base_url=runtime_base_url,
        )

    detail = f"{selected_model.display} is served by {runtime_model_id}."
    config.runtime_is_ready = True
    config.runtime_model_id = runtime_model_id
    config.last_runtime_error = ""
    config.last_runtime_check_at = now
    config.save(
        update_fields=[
            "runtime_is_ready",
            "runtime_model_id",
            "last_runtime_error",
            "last_runtime_check_at",
            "updated_at",
        ]
    )
    return SummaryRuntimeState(
        True,
        detail,
        selected_model=selected_model,
        runtime_model_id=runtime_model_id,
        runtime_base_url=runtime_base_url,
    )


def summary_runtime_is_ready(config: LLMSummaryConfig) -> bool:
    """Return whether the summarizer has a selected, probed runtime-backed model."""

    return bool(
        config.is_active
        and config.backend == LLMSummaryConfig.Backend.LLAMA_CPP_SERVER
        and get_selected_summary_model(config) is not None
        and config.runtime_is_ready
        and resolve_runtime_model_id(config)
    )


def sync_summary_suite_feature(config: LLMSummaryConfig) -> None:
    """Mirror summary runtime settings into the suite feature metadata."""

    from apps.features.models import Feature
    from apps.features.parameters import set_feature_parameter_values
    from apps.nodes.models import Node, NodeFeature
    from apps.services.lifecycle import write_lifecycle_config

    lcd_node_feature = NodeFeature.objects.filter(slug="lcd-screen").first()
    suite_feature, _created = Feature.objects.get_or_create(
        slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
        defaults={
            "display": "LLM Summary Suite",
            "source": Feature.Source.CUSTOM,
            "is_enabled": True,
            "node_feature": lcd_node_feature,
        },
    )
    updated_fields: set[str] = set()
    if suite_feature.node_feature_id != (lcd_node_feature.pk if lcd_node_feature else None):
        suite_feature.node_feature = lcd_node_feature
        updated_fields.add("node_feature")

    set_feature_parameter_values(
        suite_feature,
        {
            "selected_model": _resolve_selected_model_slug(config),
            "backend": config.backend,
            "model_path": config.model_path,
            "runtime_base_url": _resolve_runtime_base_url(config),
            "runtime_binary_path": _resolve_runtime_binary_path(config),
            "runtime_model_id": resolve_runtime_model_id(config),
        },
    )
    updated_fields.update({"metadata", "updated_at"})
    suite_feature.save(update_fields=sorted(updated_fields))
    sync_summary_runtime_service_lock(config)
    write_lifecycle_config()

    local_node = Node.get_local()
    if local_node is not None:
        local_node.sync_feature_tasks()


def ensure_local_model(
    config: LLMSummaryConfig, *, preferred_path: str | Path | None = None
) -> Path:
    """Ensure the local summary artifact directory exists and record it on ``config``."""

    if preferred_path:
        model_dir = Path(preferred_path)
    else:
        model_dir = _resolve_model_path(config)
    model_dir.mkdir(parents=True, exist_ok=True)
    sentinel = model_dir / DEFAULT_MODEL_FILE
    if not sentinel.exists():
        sentinel.write_text(
            "Local LCD summary model placeholder. Replace with actual model files.\n",
            encoding="utf-8",
        )
    config.model_path = str(model_dir)
    config.mark_installed()
    return model_dir


def _safe_offset(value: object) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def collect_recent_logs(
    config: LLMSummaryConfig,
    *,
    since: datetime,
    log_dir: Path | None = None,
) -> list[LogChunk]:
    if log_dir is None:
        log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "logs"))
    offsets = dict(config.log_offsets or {})
    chunks: list[LogChunk] = []

    if not log_dir.exists():
        logger.warning("Log directory missing: %s", log_dir)
        return []

    candidates = sorted(log_dir.rglob("*.log"))
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        since_ts = since.timestamp()
        if stat.st_mtime < since_ts:
            continue
        offset = _safe_offset(offsets.get(str(path)))
        size = stat.st_size
        if offset > size:
            offset = 0
        if size <= offset:
            offsets[str(path)] = size
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                content = handle.read()
        except OSError:
            continue
        if content:
            chunks.append(LogChunk(path=path, content=content))
        offsets[str(path)] = size

    config.log_offsets = offsets
    return chunks


def compact_log_line(line: str) -> str:
    cleaned = TIMESTAMP_RE.sub("", line)
    cleaned = UUID_RE.sub("<uuid>", cleaned)
    cleaned = HEX_RE.sub("<hex>", cleaned)
    cleaned = IP_RE.sub("<ip>", cleaned)
    cleaned = LEVEL_RE.sub(lambda match: match.group(1)[:3], cleaned)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def compact_log_chunks(chunks: Iterable[LogChunk]) -> str:
    compacted: list[str] = []
    for chunk in chunks:
        header = f"[{chunk.path.name}]"
        compacted.append(header)
        for line in chunk.content.splitlines():
            trimmed = compact_log_line(line)
            if trimmed:
                compacted.append(trimmed)
    return "\n".join(compacted)


def build_summary_prompt(compacted_logs: str, *, now: datetime) -> str:
    cutoff = (now - timedelta(minutes=4)).strftime("%H:%M")
    instructions = textwrap.dedent(
        f"""
        You summarize system logs for a 16x2 LCD. Focus on the last 4 minutes (cutoff {cutoff}).
        Highlight urgent operator actions or failures. Use shorthand, abbreviations, and ASCII symbols.
        Output 8-10 LCD screens. Each screen is two lines (subject then body).
        Aim for 14-18 chars per line, avoid scrolling when possible.
        Format:
        SCREEN 1:
        <subject line>
        <body line>
        ---
        SCREEN 2:
        <subject line>
        <body line>
        ...
        Only output the screens, no extra commentary.
        """
    ).strip()
    return f"{instructions}\n\nLOGS:\n{compacted_logs}\n"


def parse_screens(output: str) -> list[tuple[str, str]]:
    if not output:
        return []
    cleaned = [line.rstrip() for line in output.splitlines()]
    groups: list[list[str]] = []
    current: list[str] = []
    for line in cleaned:
        if not line.strip():
            continue
        if line.strip() == "---":
            if current:
                groups.append(current)
                current = []
            continue
        if line.lower().startswith("screen"):
            continue
        current.append(line)
    if current:
        groups.append(current)

    screens: list[tuple[str, str]] = []
    for group in groups:
        if len(group) < 2:
            continue
        screens.append((group[0], group[1]))
    return screens


def _normalize_line(text: str) -> str:
    normalized = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
    normalized = normalized.strip()
    if len(normalized) <= LCD_COLUMNS:
        return normalized.ljust(LCD_COLUMNS)
    trimmed = normalized[: LCD_COLUMNS - 3].rstrip()
    return f"{trimmed}...".ljust(LCD_COLUMNS)


def normalize_screens(screens: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for subject, body in screens:
        normalized.append((_normalize_line(subject), _normalize_line(body)))
    return normalized


def fixed_frame_window(screens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return exactly 10 LCD frames by truncating or padding with blanks."""

    padded = list(screens[:LCD_SUMMARY_FRAME_COUNT])
    while len(padded) < LCD_SUMMARY_FRAME_COUNT:
        padded.append((" " * LCD_COLUMNS, " " * LCD_COLUMNS))
    return padded


def render_lcd_payload(subject: str, body: str) -> str:
    return render_lcd_lock_file(subject=subject, body=body)


class LlamaCppServerSummarizer:
    """Summarize logs through a local OpenAI-compatible llama.cpp server."""

    def __init__(self, *, runtime_state: SummaryRuntimeState) -> None:
        self.runtime_state = runtime_state

    def summarize(self, prompt: str) -> str:
        response = requests.post(
            f"{self.runtime_state.runtime_base_url}/chat/completions",
            json={
                "model": self.runtime_state.runtime_model_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 256,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices", []) if isinstance(payload, dict) else []
        message = choices[0].get("message", {}) if choices else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        return str(content).strip()


def execute_log_summary_generation(*, ignore_suite_feature_gate: bool = False) -> str:
    """Generate LCD log summary output and persist latest run metadata."""

    from apps.nodes.models import Node
    from apps.screens.startup_notifications import LCD_LOW_LOCK_FILE
    from apps.tasks.tasks import (
        LocalLLMSummarizer,
        _write_lcd_frames,
    )

    node = Node.get_local()
    if not node:
        return "skipped:no-node"

    if (
        not ignore_suite_feature_gate
        and not is_suite_feature_enabled(LLM_SUMMARY_SUITE_FEATURE_SLUG, default=True)
    ):
        logger.info(
            "Skipping LCD summary automation because suite feature '%s' is disabled.",
            LLM_SUMMARY_SUITE_FEATURE_SLUG,
        )
        return "skipped:suite-feature-disabled"

    if not node.has_feature("llm-summary"):
        return "skipped:feature-disabled"
    if not node.has_feature("lcd-screen"):
        return "skipped:lcd-feature-disabled"

    config = get_summary_config()
    if not config.is_active:
        return "skipped:inactive"

    runtime_state = probe_summary_runtime(config)
    sync_summary_suite_feature(config)
    if not runtime_state.ready:
        return "skipped:model-not-ready"

    now = timezone.now()
    since = config.last_run_at or (now - timedelta(minutes=5))
    chunks = collect_recent_logs(config, since=since)
    compacted_logs = compact_log_chunks(chunks)
    if not compacted_logs:
        config.last_run_at = now
        config.save(update_fields=["last_run_at", "log_offsets", "model_path", "installed_at", "updated_at"])
        return "skipped:no-logs"

    prompt = build_summary_prompt(compacted_logs, now=now)
    if config.backend == LLMSummaryConfig.Backend.LLAMA_CPP_SERVER:
        summarizer = LlamaCppServerSummarizer(runtime_state=runtime_state)
    else:
        summarizer = LocalLLMSummarizer()
    output = summarizer.summarize(prompt)
    screens = normalize_screens(parse_screens(output))

    if not screens:
        screens = normalize_screens([("No events", "-"), ("Chk logs", "manual")])

    lock_file = Path(settings.BASE_DIR) / ".locks" / LCD_LOW_LOCK_FILE
    _write_lcd_frames(fixed_frame_window(screens), lock_file=lock_file)

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
    return f"wrote:{LCD_SUMMARY_FRAME_COUNT}"
