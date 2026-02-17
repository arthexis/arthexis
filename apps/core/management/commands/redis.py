from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand
from redis import Redis
from redis.exceptions import RedisError

from apps.core.mcp.remote_commands import remote_command

SYSTEMD_REDIS_UNITS = ("redis-server", "redis")
ENV_KEYS_PREFIXES = ("REDIS", "CELERY")
MASKED_VALUE = "****"


@dataclass(frozen=True)
class RedisConnectionReport:
    url: str
    ok: bool
    error: str | None = None


def _is_redis_url(value: str | None) -> bool:
    if not value:
        return False
    scheme = urlparse(value).scheme
    return "redis" in scheme


def _mask_redis_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.password:
        return value
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
    if parsed.password:
        userinfo = f"{userinfo}:{MASKED_VALUE}" if userinfo else f":{MASKED_VALUE}"
    netloc = f"{userinfo}@{host}{port}" if userinfo else f"{host}{port}"
    return parsed._replace(netloc=netloc).geturl()


def _mask_value(value: str) -> str:
    return _mask_redis_url(value) if _is_redis_url(value) else value


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        entries[key.strip()] = value.strip()
    return entries


def _collect_redis_urls(extra_env: dict[str, str]) -> list[str]:
    candidates = [
        getattr(settings, "CHANNEL_REDIS_URL", ""),
        getattr(settings, "OCPP_STATE_REDIS_URL", ""),
        getattr(settings, "CELERY_BROKER_URL", ""),
        getattr(settings, "CELERY_RESULT_BACKEND", ""),
        getattr(settings, "VIDEO_FRAME_REDIS_URL", ""),
        extra_env.get("CELERY_BROKER_URL", ""),
        extra_env.get("CELERY_RESULT_BACKEND", ""),
    ]
    urls: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if not _is_redis_url(value):
            continue
        if value not in seen:
            seen.add(value)
            urls.append(value)
    return urls


def _systemd_status(services: Iterable[str]) -> dict[str, str]:
    systemctl_path = shutil.which("systemctl")
    if not systemctl_path:
        return {"systemctl": "unavailable"}
    results: dict[str, str] = {}
    for service in services:
        try:
            cmd = [systemctl_path, "is-active", service]
            result = subprocess.run(cmd, capture_output=True, text=True)
        except (FileNotFoundError, OSError) as exc:
            results[service] = f"error: {exc}"
            continue
        status = result.stdout.strip() if result.stdout else "unknown"
        results[service] = status or "unknown"
    return results


def _check_redis_connection(url: str) -> RedisConnectionReport:
    try:
        client = Redis.from_url(url, decode_responses=True)
        client.ping()
    except (RedisError, OSError, ValueError) as exc:
        return RedisConnectionReport(url=url, ok=False, error=str(exc))
    return RedisConnectionReport(url=url, ok=True)


def _format_bytes(value: object) -> str:
    if isinstance(value, int):
        return f"{value:,} bytes"
    if isinstance(value, str) and value:
        return value
    return "unknown"


def _info_section(client: Redis, section: str) -> dict[str, object]:
    try:
        return client.info(section)
    except (RedisError, OSError, ValueError):
        return {}


@remote_command(description="Show Redis status and connectivity diagnostics.")
class Command(BaseCommand):
    help = "Show Redis service status and configuration"

    def add_arguments(self, parser):
        parser.add_argument(
            "--report",
            action="store_true",
            help="Show detailed Redis report including memory usage",
        )

    def handle(self, *args, **options):  # type: ignore[override]
        base_dir = Path(getattr(settings, "BASE_DIR", os.getcwd()))
        redis_env = _read_env_file(base_dir / "redis.env")
        redis_urls = _collect_redis_urls(redis_env)
        config_settings = [
            "CHANNEL_REDIS_URL",
            "OCPP_STATE_REDIS_URL",
            "CELERY_BROKER_URL",
            "CELERY_RESULT_BACKEND",
            "VIDEO_FRAME_REDIS_URL",
        ]

        self.stdout.write("Redis service status:")
        systemd_statuses = _systemd_status(SYSTEMD_REDIS_UNITS)
        for service, status in systemd_statuses.items():
            self.stdout.write(f"  {service}: {status}")

        self.stdout.write("")
        self.stdout.write("Redis configuration:")
        for setting_name in config_settings:
            raw_value = getattr(settings, setting_name, "") or "unset"
            value = _mask_value(raw_value) if raw_value != "unset" else raw_value
            self.stdout.write(f"  {setting_name}: {value}")
        if redis_env:
            self.stdout.write("  redis.env:")
            filtered_env = {
                key: value
                for key, value in redis_env.items()
                if key.startswith(ENV_KEYS_PREFIXES)
            }
            if filtered_env:
                for key, value in sorted(filtered_env.items()):
                    self.stdout.write(f"    {key}={_mask_value(value)}")
            else:
                self.stdout.write("    (no Redis-related entries)")
        else:
            self.stdout.write("  redis.env: not found or empty")

        self.stdout.write("")
        if redis_urls:
            report = _check_redis_connection(redis_urls[0])
            if report.ok:
                self.stdout.write(
                    f"Redis connectivity: OK ({_mask_redis_url(report.url)})"
                )
            else:
                self.stdout.write(
                    f"Redis connectivity: FAILED ({_mask_redis_url(report.url)}) -> {report.error}"
                )
        else:
            self.stdout.write(
                "Redis connectivity: unavailable (no Redis URLs configured)"
            )

        if not options.get("report"):
            return

        self.stdout.write("")
        self.stdout.write("Redis report:")
        if not redis_urls:
            self.stdout.write("  No Redis URLs available for report.")
            return

        url = redis_urls[0]
        try:
            client = Redis.from_url(url, decode_responses=True)
            memory_info = _info_section(client, "memory")
            server_info = _info_section(client, "server")
            keyspace_info = _info_section(client, "keyspace")
        except (RedisError, OSError, ValueError) as exc:
            self.stdout.write(
                f"  Unable to fetch report from {_mask_redis_url(url)}: {exc}"
            )
            return

        self.stdout.write(f"  URL: {_mask_redis_url(url)}")
        self.stdout.write(f"  Version: {server_info.get('redis_version', 'unknown')}")
        self.stdout.write(
            f"  Uptime (days): {server_info.get('uptime_in_days', 'unknown')}"
        )
        if keyspace_info:
            self.stdout.write("  Keyspace:")
            for key, value in keyspace_info.items():
                self.stdout.write(f"    {key}: {value}")
        else:
            self.stdout.write("  Keyspace: unavailable")

        self.stdout.write("  Memory usage:")
        self.stdout.write(
            f"    Used: {_format_bytes(memory_info.get('used_memory_human') or memory_info.get('used_memory'))}"
        )
        self.stdout.write(
            f"    Peak: {_format_bytes(memory_info.get('used_memory_peak_human') or memory_info.get('used_memory_peak'))}"
        )
        self.stdout.write(
            f"    RSS: {_format_bytes(memory_info.get('used_memory_rss_human') or memory_info.get('used_memory_rss'))}"
        )
        self.stdout.write(
            f"    Max memory: {_format_bytes(memory_info.get('maxmemory_human') or memory_info.get('maxmemory'))}"
        )
        if "maxmemory_policy" in memory_info:
            self.stdout.write(
                f"    Eviction policy: {memory_info.get('maxmemory_policy')}"
            )
