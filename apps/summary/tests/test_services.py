from datetime import datetime, timezone
from types import SimpleNamespace

from apps.summary import dense_lcd, services
from apps.tasks import tasks as task_services
from apps.tasks.tasks import _write_lcd_frames


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

    assert "Think in 32 visible cells per screen" in prompt
    assert "Row 1 is the log extract" in prompt
    assert '"12 ln/5m" for log lines' in prompt
    assert 'Never write "line" or "lines" on row 2' in prompt
    assert "Shorten words aggressively" in prompt
    assert "Do not emit routine Host screens" in prompt
    assert "LOGS:\nlog line" in prompt


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

    assert frames == [("Journal failed 3", "12 ln/5m   ERROR")]


def test_normalize_screens_adds_window_to_repeat_counts() -> None:
    frames = services.normalize_screens([("refresh usb lcd", "10x       normal")])

    assert frames == [("refresh usb lcd ", "10x/5m    NORMAL")]


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

    assert frames[0] == ("ABCDEFGHIJKLMNOP", "1 ln/5m    ERROR")
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
    monkeypatch.setattr(services, "collect_recent_logs", lambda config, since: [])

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
        "collect_recent_logs",
        lambda config, since: [
            services.LogChunk(path=tmp_path / "journal.log", content="ERR hi")
        ],
    )
    monkeypatch.setattr(services, "build_summary_prompt", lambda logs, now: "prompt")
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
