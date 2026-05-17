import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from django.utils import timezone as django_timezone

from apps.summary import dense_lcd, services
from apps.tasks import tasks as task_services
from apps.tasks.tasks import _write_lcd_frames


def _summary_window(
    *,
    minutes: int = 60,
    min_minutes: int = 5,
    max_minutes: int = 60,
    reasons: tuple[str, ...] = (),
) -> services.SummaryContextWindow:
    return services.SummaryContextWindow(
        minutes=minutes,
        label=f"{minutes}m",
        min_minutes=min_minutes,
        max_minutes=max_minutes,
        reasons=reasons,
    )


def _log_source(*, max_bytes: int = 12_000) -> services.SummarySource:
    return services.SummarySource(
        name="logs",
        group="logs",
        priority=10,
        max_bytes=max_bytes,
        collector=services._collect_log_file_source,
    )


def test_fixed_frame_window_does_not_pad_blank_frames() -> None:
    frames = services.fixed_frame_window([("A", "B"), ("C", "D")])

    assert frames == [("A", "B"), ("C", "D")]


def test_filter_redundant_lcd_summary_screens_drops_host_resource_frame() -> None:
    frames = services.filter_redundant_lcd_summary_screens(
        [
            ("Host", "t65C d51% m44%"),
            ("HOST:gway-001", "routine status"),
            ("Host", "failed journal writer"),
            ("Status", "0 failed units"),
            ("USB key", "sda1 ro bastion"),
        ]
    )

    assert frames == [
        ("Host", "failed journal writer"),
        ("Status", "0 failed units"),
        ("USB key", "sda1 ro bastion"),
    ]


def test_build_summary_prompt_excludes_dedicated_resource_screens() -> None:
    prompt = services.build_summary_prompt("log line", now=datetime(2026, 5, 3))

    assert "LCD_CONTEXT_WINDOW_LABEL: 60m" in prompt
    assert "Think in 32 visible cells per screen" in prompt
    assert "Row 1 is the log extract" in prompt
    assert "Focus on the last 60 minutes" in prompt
    assert "Older warning, error, and critical lines from the last 60 minutes" in prompt
    assert '"12 ln/60m" for log lines' in prompt
    assert 'Never write "line" or "lines" on row 2' in prompt
    assert "Shorten words aggressively" in prompt
    assert "Do not emit routine Host screens" in prompt
    assert "LOGS:\nlog line" in prompt


def test_collect_recent_logs_uses_registered_log_and_state_sources(
    monkeypatch, settings, tmp_path
) -> None:
    log_dir = tmp_path / "logs"
    lock_dir = tmp_path / ".locks"
    log_dir.mkdir()
    lock_dir.mkdir()
    log_path = log_dir / "arthexis.log"
    state_path = lock_dir / "lcd-channels.lck"
    rfid_path = lock_dir / "rfid-scan.json"
    log_path.write_text("ERROR worker failed\n", encoding="utf-8")
    state_path.write_text("LCD channels active\n", encoding="utf-8")
    rfid_path.write_text('{"label": "idle"}\n', encoding="utf-8")

    config = SimpleNamespace(log_offsets={})
    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(
        services,
        "_summary_state_paths",
        lambda base_dir: [state_path, rfid_path],
    )
    sources = [
        services.SummarySource(
            name="logs",
            group="logs",
            priority=10,
            max_bytes=1024,
            collector=services._collect_log_file_source,
        ),
        services.SummarySource(
            name="state",
            group="state",
            priority=20,
            max_bytes=1024,
            collector=services._collect_state_file_source,
        ),
    ]

    chunks = services.collect_recent_logs(
        config,
        since=datetime(1970, 1, 1, tzinfo=timezone.utc),
        log_dir=log_dir,
        sources=sources,
    )
    compacted = services.compact_log_chunks(chunks)

    assert "[arthexis.log]" in compacted
    assert "[state:.locks:lcd-channels.lck]" in compacted
    assert "[state:.locks:rfid-scan.json]" in compacted
    assert "ERR worker failed" in compacted
    assert "LCD channels active" in compacted
    assert config.log_offsets[str(log_path)] == log_path.stat().st_size


def test_summary_state_paths_excludes_generated_lcd_summary_locks(tmp_path) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    generated_summary = lock_dir / "lcd-summary"
    generated_summary_slot = lock_dir / "lcd-summary-1"
    channel_lock = lock_dir / "lcd-channels.lck"
    generated_summary.write_text("model output\n", encoding="utf-8")
    generated_summary_slot.write_text("model output\n", encoding="utf-8")
    channel_lock.write_text("channels\n", encoding="utf-8")

    paths = services._summary_state_paths(tmp_path)

    assert generated_summary not in paths
    assert generated_summary_slot not in paths
    assert channel_lock in paths


def test_get_summary_sources_respects_configured_groups_and_byte_budget(
    monkeypatch,
) -> None:
    values = {
        "enabled_sources": "state",
        "max_source_bytes": "999999",
    }
    monkeypatch.setattr(
        services,
        "get_feature_parameter",
        lambda slug, key, fallback="": values.get(key, fallback),
    )

    sources = services.get_summary_sources()

    assert [source.name for source in sources] == ["suite-state-files"]
    assert sources[0].max_bytes == services.SUMMARY_STATE_SOURCE_MAX_BYTES


def test_get_summary_sources_falls_back_when_groups_are_unknown(
    monkeypatch,
) -> None:
    values = {
        "enabled_sources": "log,statee",
        "max_source_bytes": "12000",
    }
    monkeypatch.setattr(
        services,
        "get_feature_parameter",
        lambda slug, key, fallback="": values.get(key, fallback),
    )

    sources = services.get_summary_sources()

    assert [source.group for source in sources] == ["logs", "state", "journal", "journal"]


def test_collect_log_file_source_enforces_total_log_byte_budget(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    first_log = log_dir / "a.log"
    second_log = log_dir / "b.log"
    first_log.write_text("a" * 5, encoding="utf-8")
    second_log.write_text("b" * 5, encoding="utf-8")
    config = SimpleNamespace(log_offsets={})
    context = services.SummarySourceContext(
        config=config,
        since=datetime(1970, 1, 1, tzinfo=timezone.utc),
        base_dir=tmp_path,
        log_dir=log_dir,
    )
    source = services.SummarySource(
        name="logs",
        group="logs",
        priority=10,
        max_bytes=5,
        collector=services._collect_log_file_source,
    )

    chunks = services._collect_log_file_source(context, source)

    assert [chunk.path.name for chunk in chunks] == ["a.log"]
    assert chunks[0].content == "a" * 5
    assert config.log_offsets[str(second_log)] == 0


def test_collect_log_file_source_retries_budget_deferred_logs(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    first_log = log_dir / "a.log"
    second_log = log_dir / "b.log"
    first_log.write_text("a" * 5, encoding="utf-8")
    second_log.write_text("b" * 5, encoding="utf-8")
    config = SimpleNamespace(log_offsets={})
    context = services.SummarySourceContext(
        config=config,
        since=datetime(1970, 1, 1, tzinfo=timezone.utc),
        base_dir=tmp_path,
        log_dir=log_dir,
    )
    source = services.SummarySource(
        name="logs",
        group="logs",
        priority=10,
        max_bytes=5,
        collector=services._collect_log_file_source,
    )

    services._collect_log_file_source(context, source)
    first_log.write_text("a" * 5, encoding="utf-8")
    chunks = services._collect_log_file_source(context, source)

    assert [chunk.path.name for chunk in chunks] == ["b.log"]
    assert chunks[0].content == "b" * 5


def test_collect_state_file_source_enforces_total_byte_budget(
    monkeypatch, tmp_path
) -> None:
    first_state = tmp_path / ".locks" / "lcd-channels.lck"
    second_state = tmp_path / ".locks" / "rfid-scan.json"
    first_state.parent.mkdir()
    first_state.write_text("a" * 5, encoding="utf-8")
    second_state.write_text("b" * 5, encoding="utf-8")
    context = services.SummarySourceContext(
        config=SimpleNamespace(log_offsets={}),
        since=datetime(1970, 1, 1, tzinfo=timezone.utc),
        base_dir=tmp_path,
        log_dir=tmp_path / "logs",
    )
    source = services.SummarySource(
        name="state",
        group="state",
        priority=20,
        max_bytes=5,
        collector=services._collect_state_file_source,
    )
    monkeypatch.setattr(
        services,
        "_summary_state_paths",
        lambda base_dir: [first_state, second_state],
    )

    chunks = services._collect_state_file_source(context, source)

    assert [chunk.path.name for chunk in chunks] == ["state:.locks:lcd-channels.lck"]
    assert "b" * 5 not in "\n".join(chunk.content for chunk in chunks)


def test_collect_state_file_source_sanitizes_rfid_scan_state(
    monkeypatch, tmp_path
) -> None:
    rfid_state = tmp_path / ".locks" / "rfid-scan.json"
    rfid_state.parent.mkdir()
    rfid_state.write_text(
        '{"uid":"1","keys":{"a":"A0A1A2A3A4A5"},"dump":{"b":"c"},"deep_read":true}\n',
        encoding="utf-8",
    )
    context = services.SummarySourceContext(
        config=SimpleNamespace(log_offsets={}),
        since=datetime(1970, 1, 1, tzinfo=timezone.utc),
        base_dir=tmp_path,
        log_dir=tmp_path / "logs",
    )
    source = services.SummarySource(
        name="state",
        group="state",
        priority=20,
        max_bytes=1024,
        collector=services._collect_state_file_source,
    )
    monkeypatch.setattr(services, "_summary_state_paths", lambda base_dir: [rfid_state])

    chunks = services._collect_state_file_source(context, source)

    assert len(chunks) == 1
    assert '"uid": "1"' in chunks[0].content
    assert "keys" not in chunks[0].content
    assert "dump" not in chunks[0].content
    assert "deep_read" not in chunks[0].content


def test_systemctl_failed_source_skips_clean_output(monkeypatch, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(
        services,
        "_run_summary_command",
        lambda command: "0 loaded units listed.",
    )
    context = services.SummarySourceContext(
        config=SimpleNamespace(log_offsets={}),
        since=datetime(1970, 1, 1, tzinfo=timezone.utc),
        base_dir=tmp_path,
        log_dir=tmp_path / "logs",
    )
    source = services.SummarySource(
        name="systemctl-failed",
        group="journal",
        priority=30,
        max_bytes=1024,
        collector=services._collect_systemctl_failed_source,
    )

    assert services._collect_systemctl_failed_source(context, source) == []


def test_journal_warning_source_collects_suite_unit_warnings(
    monkeypatch, settings, tmp_path
) -> None:
    settings.BASE_DIR = tmp_path
    calls = []

    def fake_run(command):
        calls.append(command)
        if "lcd-arthexis.service" in command:
            return "2026-05-03T12:00:00 warning lcd refresh failed"
        return "-- No entries --"

    monkeypatch.setattr(services, "_run_summary_command", fake_run)
    monkeypatch.setattr(
        services,
        "SUMMARY_JOURNAL_UNITS",
        ("lcd-arthexis.service", "rfid-arthexis.service"),
    )
    context = services.SummarySourceContext(
        config=SimpleNamespace(log_offsets={}),
        since=datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc),
        base_dir=tmp_path,
        log_dir=tmp_path / "logs",
    )
    source = services.SummarySource(
        name="journal",
        group="journal",
        priority=40,
        max_bytes=1024,
        collector=services._collect_journal_warning_source,
    )

    chunks = services._collect_journal_warning_source(context, source)

    assert [chunk.path.name for chunk in chunks] == ["journal:lcd-arthexis.service"]
    assert "warning lcd refresh failed" in chunks[0].content
    assert "2026-05-03 12:00:00+00:00" in calls[0]
    assert "--priority" in calls[0]
    assert "emerg..warning" in calls[0]


def test_journal_warning_source_enforces_total_byte_budget(
    monkeypatch, settings, tmp_path
) -> None:
    settings.BASE_DIR = tmp_path

    def fake_run(command):
        if "lcd-arthexis.service" in command:
            return "a" * 5
        if "rfid-arthexis.service" in command:
            return "b" * 5
        return "-- No entries --"

    monkeypatch.setattr(services, "_run_summary_command", fake_run)
    monkeypatch.setattr(
        services,
        "SUMMARY_JOURNAL_UNITS",
        ("lcd-arthexis.service", "rfid-arthexis.service"),
    )
    context = services.SummarySourceContext(
        config=SimpleNamespace(log_offsets={}),
        since=datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc),
        base_dir=tmp_path,
        log_dir=tmp_path / "logs",
    )
    source = services.SummarySource(
        name="journal",
        group="journal",
        priority=40,
        max_bytes=5,
        collector=services._collect_journal_warning_source,
    )

    chunks = services._collect_journal_warning_source(context, source)

    assert [chunk.path.name for chunk in chunks] == ["journal:lcd-arthexis.service"]
    assert chunks[0].content == "a" * 5


def test_parse_screens_accepts_single_line_colon_buffers() -> None:
    screens = services.parse_screens("SCREEN 1:\nERR:svc fail -> rst\n---\n")

    assert screens == [("ERR:svc fail -> rst", "")]


def test_parse_screens_ignores_single_line_prose() -> None:
    screens = services.parse_screens("Here is your summary:\n---\nERR:svc fail\n")

    assert screens == [("ERR:svc fail", "")]


def test_normalize_screens_flows_header_and_message_across_buffer() -> None:
    frames = services.normalize_screens(
        [("ERR", "scheduler raised unexpected reboot required")]
    )

    assert frames == [("ERR             ", "scheduler raised")]
    assert len(frames[0][0]) == 16
    assert len(frames[0][1]) == 16


def test_normalize_screens_keeps_log_extract_and_status_on_separate_rows() -> None:
    frames = services.normalize_screens([("Journal failed 3", "12 lines      error")])

    assert frames == [("Journal failed 3", "12 ln/60m  ERROR")]


def test_normalize_screens_adds_window_to_repeat_counts() -> None:
    frames = services.normalize_screens([("refresh usb lcd", "10x       normal")])

    assert frames == [("refresh usb lcd ", "10x/60m   NORMAL")]


def test_normalize_screens_preserves_existing_dynamic_window() -> None:
    frames = services.normalize_screens([("Boom", "1 ln/5m error")])

    assert frames == [("Boom            ", "1 ln/5m    ERROR")]


def test_normalize_screens_preserves_existing_inline_header() -> None:
    frames = services.normalize_screens([("ERR:svc fail", "manual rst")])

    assert frames == [("ERR:svc fail    ", "manual rst      ")]


def test_normalize_screens_preserves_prewrapped_inline_buffer() -> None:
    frames = services.normalize_screens([("ERR2 WRN1:Panic", "failure")])

    assert frames == [("ERR2 WRN1:Panic ", "failure         ")]


def test_deterministic_summary_round_trips_thirty_two_cell_buffer() -> None:
    from apps.tasks.tasks import LocalLLMSummarizer

    output = LocalLLMSummarizer().summarize(
        "\n".join(
            [
                "LOGS:",
                "ERR apps.demo: ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            ]
        )
    )

    frames = services.normalize_screens(services.parse_screens(output))

    assert frames[0] == ("ABCDEFGHIJKLMNOP", "1 ln/60m   ERROR")
    assert len(frames[0][0]) == 16
    assert len(frames[0][1]) == 16


def test_filter_redundant_lcd_summary_screens_handles_inline_headers() -> None:
    frames = services.filter_redundant_lcd_summary_screens(
        [
            ("HOST:t65C d51% m44%", ""),
            ("Host:gway-001", "active"),
            ("HOST:gway-001", "down"),
            ("ERR:svc failed", "manual"),
        ]
    )

    assert frames == [("HOST:gway-001", "down"), ("ERR:svc failed", "manual")]


def test_summary_frames_are_written_with_expiry(tmp_path) -> None:
    expires_at = datetime(2026, 5, 3, 14, 30, tzinfo=timezone.utc)

    _write_lcd_frames(
        [("OK", "No errors")],
        lock_file=tmp_path / "lcd-summary",
        expires_at=expires_at,
    )

    assert (tmp_path / "lcd-summary").read_text(encoding="utf-8").splitlines() == [
        "OK",
        "No errors",
        "2026-05-03T14:30:00+00:00",
    ]


def test_summary_context_window_bounds_are_configurable(monkeypatch) -> None:
    values = {"min_context_minutes": "10", "max_context_minutes": "90"}

    monkeypatch.setattr(
        services,
        "get_feature_parameter",
        lambda slug, key, fallback="": values.get(key, fallback),
    )

    assert services.get_summary_context_window_bounds() == (10, 90)


def test_summary_context_window_uses_env_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("ARTHEXIS_LLM_SUMMARY_MIN_CONTEXT_MINUTES", "8")
    monkeypatch.setenv("ARTHEXIS_LLM_SUMMARY_MAX_CONTEXT_MINUTES", "75")
    monkeypatch.setattr(
        services,
        "get_feature_parameter",
        lambda slug, key, fallback="": fallback,
    )

    assert services.get_summary_context_window_bounds() == (8, 75)


def test_summary_context_window_shrinks_under_heat(monkeypatch) -> None:
    monkeypatch.setattr(services, "get_summary_context_window_bounds", lambda: (5, 60))
    monkeypatch.setattr(services, "_read_cpu_temperature_c", lambda: 82)
    monkeypatch.setattr(services, "_read_load_pressure_ratio", lambda: None)
    monkeypatch.setattr(services, "_read_memory_available_percent", lambda: None)

    window = services.resolve_summary_context_window()

    assert window.minutes == 5
    assert window.label == "5m"
    assert window.min_minutes == 5
    assert window.max_minutes == 60
    assert window.reasons == ("temp=82C",)


def test_summary_context_window_respects_configured_bounds_under_heat(
    monkeypatch,
) -> None:
    monkeypatch.setattr(services, "get_summary_context_window_bounds", lambda: (10, 120))
    monkeypatch.setattr(services, "_read_cpu_temperature_c", lambda: 76)
    monkeypatch.setattr(services, "_read_load_pressure_ratio", lambda: None)
    monkeypatch.setattr(services, "_read_memory_available_percent", lambda: None)

    window = services.resolve_summary_context_window()

    assert window.minutes == 30
    assert window.label == "30m"
    assert window.min_minutes == 10
    assert window.max_minutes == 120


def test_collect_recent_logs_uses_timestamped_context_window(tmp_path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "\n".join(
            [
                "2026-05-03 10:59:59,999 [INFO] old line",
                "old continuation",
                "2026-05-03 11:00:00,000 [INFO] cutoff line",
                "cutoff continuation",
                "2026-05-03T11:30:00 [ERROR] newer line",
            ]
        ),
        encoding="utf-8",
    )

    class FakeConfig:
        log_offsets = {}

    since = django_timezone.make_aware(datetime(2026, 5, 3, 11, 0))

    chunks = services.collect_recent_logs(
        FakeConfig(),
        since=since,
        log_dir=tmp_path,
        sources=[_log_source()],
    )

    assert len(chunks) == 1
    content = chunks[0].content
    assert "old line" not in content
    assert "old continuation" not in content
    assert "cutoff line" in content
    assert "cutoff continuation" in content
    assert "newer line" in content


def test_collect_recent_logs_reads_only_new_bytes_after_offset(tmp_path) -> None:
    log_path = tmp_path / "app.log"
    old_content = "\n".join(
        [
            "2026-05-03 11:10:00,000 [ERROR] old line",
            "old continuation",
            "",
        ]
    )
    log_path.write_text(
        old_content + "2026-05-03 11:30:00,000 [ERROR] newer line\n",
        encoding="utf-8",
    )

    class FakeConfig:
        log_offsets = {str(log_path): len(old_content.encode("utf-8"))}

    chunks = services.collect_recent_logs(
        FakeConfig(),
        since=django_timezone.make_aware(datetime(2026, 5, 3, 11, 0)),
        log_dir=tmp_path,
        sources=[_log_source()],
    )

    assert len(chunks) == 1
    assert "old line" not in chunks[0].content
    assert "newer line" in chunks[0].content


def test_collect_recent_logs_keeps_unread_offsets_past_mtime_cutoff(
    tmp_path,
) -> None:
    log_path = tmp_path / "app.log"
    old_content = "2026-05-03 11:10:00,000 [ERROR] old line\n"
    log_path.write_text(
        old_content + "2026-05-03 11:30:00,000 [ERROR] unread line\n",
        encoding="utf-8",
    )
    os.utime(log_path, (1, 1))

    class FakeConfig:
        log_offsets = {str(log_path): len(old_content.encode("utf-8"))}

    chunks = services.collect_recent_logs(
        FakeConfig(),
        since=django_timezone.make_aware(datetime(2026, 5, 3, 11, 0)),
        log_dir=tmp_path,
        sources=[_log_source()],
    )

    assert len(chunks) == 1
    assert "old line" not in chunks[0].content
    assert "unread line" in chunks[0].content


def test_collect_recent_logs_preserves_unread_timestamped_backlog(
    tmp_path,
) -> None:
    log_path = tmp_path / "app.log"
    old_content = "2026-05-03 09:00:00,000 [INFO] consumed line\n"
    log_path.write_text(
        old_content + "2026-05-03 10:00:00,000 [ERROR] delayed unread line\n",
        encoding="utf-8",
    )

    class FakeConfig:
        log_offsets = {str(log_path): len(old_content.encode("utf-8"))}

    chunks = services.collect_recent_logs(
        FakeConfig(),
        since=django_timezone.make_aware(datetime(2026, 5, 3, 11, 0)),
        attention_since=django_timezone.make_aware(datetime(2026, 5, 3, 11, 0)),
        log_dir=tmp_path,
        sources=[_log_source()],
    )

    assert len(chunks) == 1
    assert "consumed line" not in chunks[0].content
    assert "delayed unread line" in chunks[0].content


def test_collect_recent_logs_does_not_replay_timestamp_free_logs(
    tmp_path,
) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text("old warning without timestamp\n", encoding="utf-8")

    class FakeConfig:
        log_offsets = {}

    config = FakeConfig()
    since = django_timezone.make_aware(datetime(2026, 5, 3, 11, 0))

    first_chunks = services.collect_recent_logs(
        config,
        since=since,
        log_dir=tmp_path,
        sources=[_log_source()],
    )
    second_chunks = services.collect_recent_logs(
        config,
        since=since,
        log_dir=tmp_path,
        sources=[_log_source()],
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("new warning without timestamp\n")
    third_chunks = services.collect_recent_logs(
        config,
        since=since,
        log_dir=tmp_path,
        sources=[_log_source()],
    )

    assert len(first_chunks) == 1
    assert first_chunks[0].content.splitlines() == ["old warning without timestamp"]
    assert second_chunks == []
    assert len(third_chunks) == 1
    assert third_chunks[0].content.splitlines() == ["new warning without timestamp"]


def test_collect_recent_logs_keeps_attention_from_full_attention_window(
    tmp_path,
) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "\n".join(
            [
                "2026-05-03 11:10:00,000 [INFO] old routine",
                "2026-05-03 11:30:00,000 [ERROR] old error",
                "error continuation",
                "2026-05-03 11:59:00,000 [INFO] recent routine",
            ]
        ),
        encoding="utf-8",
    )

    class FakeConfig:
        log_offsets = {}

    chunks = services.collect_recent_logs(
        FakeConfig(),
        since=django_timezone.make_aware(datetime(2026, 5, 3, 11, 55)),
        attention_since=django_timezone.make_aware(datetime(2026, 5, 3, 11, 0)),
        log_dir=tmp_path,
        sources=[_log_source()],
    )

    assert len(chunks) == 1
    content = chunks[0].content
    assert "old routine" not in content
    assert "old error" in content
    assert "error continuation" in content
    assert "recent routine" in content


def test_generation_uses_adaptive_summary_context_window(
    monkeypatch, settings, tmp_path
) -> None:
    from apps.nodes.models import Node

    class FakeNode:
        role = SimpleNamespace(name="Control")

        def has_feature(self, slug: str) -> bool:
            return slug == "llm-summary"

    class FakeConfig:
        is_active = True
        last_run_at = django_timezone.make_aware(datetime(2026, 5, 3, 11, 55))
        log_offsets = {}

        def save(self, *, update_fields):
            self.update_fields = update_fields

    fake_now = django_timezone.make_aware(datetime(2026, 5, 3, 12, 0))
    captured = {}

    def fake_collect_recent_logs(config, *, since, attention_since=None):
        captured["since"] = since
        captured["attention_since"] = attention_since
        return []

    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: FakeNode()))
    monkeypatch.setattr(
        services,
        "is_suite_feature_enabled",
        lambda slug, default=True: True,
    )
    monkeypatch.setattr(services, "get_summary_config", lambda: FakeConfig())
    monkeypatch.setattr(services, "ensure_local_model", lambda config: None)
    monkeypatch.setattr(services.timezone, "now", lambda: fake_now)
    monkeypatch.setattr(
        services,
        "resolve_summary_context_window",
        lambda: _summary_window(minutes=5, reasons=("temp=82C",)),
    )
    monkeypatch.setattr(services, "collect_recent_logs", fake_collect_recent_logs)

    result = services.execute_log_summary_generation()

    assert result == "skipped:no-logs"
    assert captured["since"] == fake_now - timedelta(minutes=5)
    assert captured["attention_since"] == fake_now - timedelta(minutes=60)


def test_no_log_generation_preserves_low_channel_messages(
    monkeypatch, settings, tmp_path
) -> None:
    from apps.nodes.models import Node

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd-low").write_text("old\nsummary\n", encoding="utf-8")
    (lock_dir / "lcd-low-1").write_text("old\nsummary\n", encoding="utf-8")

    class FakeNode:
        role = SimpleNamespace(name="Control")

        def has_feature(self, slug: str) -> bool:
            return slug == "llm-summary"

    class FakeConfig:
        is_active = True
        last_run_at = None

        def save(self, *, update_fields):
            self.update_fields = update_fields

    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: FakeNode()))
    monkeypatch.setattr(
        services,
        "is_suite_feature_enabled",
        lambda slug, default=True: True,
    )
    monkeypatch.setattr(services, "get_summary_config", lambda: FakeConfig())
    monkeypatch.setattr(services, "ensure_local_model", lambda config: None)
    monkeypatch.setattr(
        services,
        "resolve_summary_context_window",
        lambda: _summary_window(),
    )
    monkeypatch.setattr(
        services,
        "collect_recent_logs",
        lambda config, *, since, attention_since=None: [],
    )

    result = services.execute_log_summary_generation()

    assert result == "skipped:no-logs"
    assert (lock_dir / "lcd-low").exists()
    assert (lock_dir / "lcd-low-1").exists()


def test_suite_gate_skip_preserves_low_channel_messages(
    monkeypatch, settings, tmp_path
) -> None:
    from apps.nodes.models import Node

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd-low").write_text("old\nsummary\n", encoding="utf-8")
    (lock_dir / "lcd-low-1").write_text("old\nsummary\n", encoding="utf-8")

    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(
        Node,
        "get_local",
        staticmethod(lambda: SimpleNamespace(role=SimpleNamespace(name="Control"))),
    )
    monkeypatch.setattr(
        services,
        "is_suite_feature_enabled",
        lambda slug, default=True: False,
    )

    result = services.execute_log_summary_generation()

    assert result == "skipped:suite-feature-disabled"
    assert (lock_dir / "lcd-low").exists()
    assert (lock_dir / "lcd-low-1").exists()


def test_generation_without_lcd_feature_does_not_write_summary_lock(
    monkeypatch, settings, tmp_path
) -> None:
    from apps.nodes.models import Node

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd-summary").write_text("stale\nsummary\n", encoding="utf-8")
    (lock_dir / "lcd-summary-1").write_text("stale\nsummary\n", encoding="utf-8")
    (lock_dir / "lcd-summary-2").write_text("stale\nsummary\n", encoding="utf-8")

    class FakeNode:
        role = SimpleNamespace(name="Control")

        def has_feature(self, slug: str) -> bool:
            return slug in {"llm-summary"}

    class FakeConfig:
        is_active = True
        last_run_at = None

        def save(self, *, update_fields):
            self.update_fields = update_fields

    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: FakeNode()))
    monkeypatch.setattr(
        services,
        "is_suite_feature_enabled",
        lambda slug, default=True: True,
    )
    monkeypatch.setattr(services, "get_summary_config", lambda: FakeConfig())
    monkeypatch.setattr(services, "ensure_local_model", lambda config: None)
    monkeypatch.setattr(
        services,
        "resolve_summary_context_window",
        lambda: _summary_window(minutes=5),
    )
    monkeypatch.setattr(
        services,
        "collect_recent_logs",
        lambda config, *, since, attention_since=None: [
            services.LogChunk(path=tmp_path / "journal.log", content="ERR hi")
        ],
    )
    monkeypatch.setattr(
        services,
        "build_summary_prompt",
        lambda logs, *, now, window=None: "prompt",
    )
    monkeypatch.setattr(
        services,
        "parse_screens",
        lambda output: [("Issue", "1 line error")],
    )

    class FakeSummarizer:
        def summarize(self, prompt: str) -> str:
            return "SCREEN 1:\nIssue\n1 line error"

    monkeypatch.setattr(task_services, "LocalLLMSummarizer", FakeSummarizer)

    result = services.execute_log_summary_generation()

    assert result == "wrote:1"
    assert not (lock_dir / "lcd-summary").exists()
    assert not (lock_dir / "lcd-summary-1").exists()
    assert not (lock_dir / "lcd-summary-2").exists()


def test_dense_frames_from_prompt_builds_summary_frame() -> None:
    prompt = "\n".join(
        [
            "Instructions",
            "LOGS:",
            "[journal.log]",
            "ERROR worker failed to refresh inventory",
            "WARNING retry scheduled",
        ]
    )

    frames = dense_lcd.dense_frames_from_prompt(prompt)

    assert frames[0] == ("2 ln", "ERR 1")
    assert frames[1] == ("ERR journal", "ERR worker faile")


def test_log_summary_generation_skips_non_control_node(monkeypatch, settings, tmp_path):
    from apps.nodes.models import Node

    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(
        Node,
        "get_local",
        staticmethod(lambda: SimpleNamespace(role=SimpleNamespace(name="Terminal"))),
    )

    assert services.execute_log_summary_generation() == "skipped:non-control-node"


def test_dense_lcd_summary_does_not_replay_stale_prompt(
    monkeypatch, settings, tmp_path
):
    from apps.nodes.models import Node

    settings.BASE_DIR = tmp_path

    class FakeNode:
        role = SimpleNamespace(name="Control")

        def has_feature(self, slug: str) -> bool:
            return slug in {"llm-summary", "lcd-screen"}

    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: FakeNode()))
    monkeypatch.setattr(
        dense_lcd,
        "is_suite_feature_enabled",
        lambda slug, default=True: True,
    )
    monkeypatch.setattr(
        dense_lcd,
        "execute_log_summary_generation",
        lambda *, ignore_suite_feature_gate=False: "skipped:no-logs",
    )
    monkeypatch.setattr(
        dense_lcd,
        "get_summary_config",
        lambda: pytest.fail("stale prompt should not be read"),
    )

    assert dense_lcd.execute_dense_lcd_summary() == "skipped:no-logs"
