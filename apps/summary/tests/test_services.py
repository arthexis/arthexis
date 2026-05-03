from datetime import datetime, timezone

from apps.summary import services
from apps.tasks.tasks import _write_lcd_frames


def test_fixed_frame_window_does_not_pad_blank_frames() -> None:
    frames = services.fixed_frame_window([("A", "B"), ("C", "D")])

    assert frames == [("A", "B"), ("C", "D")]


def test_filter_redundant_lcd_summary_screens_drops_host_resource_frame() -> None:
    frames = services.filter_redundant_lcd_summary_screens(
        [
            ("Host", "t65C d51% m44%"),
            ("Status", "0 failed units"),
            ("USB key", "sda1 ro bastion"),
        ]
    )

    assert frames == [
        ("Status", "0 failed units"),
        ("USB key", "sda1 ro bastion"),
    ]


def test_build_summary_prompt_excludes_dedicated_resource_screens() -> None:
    prompt = services.build_summary_prompt("log line", now=datetime(2026, 5, 3))

    assert "Think in 32 visible cells per screen" in prompt
    assert 'then a single ":" and continue the message immediately' in prompt
    assert "Shorten words aggressively" in prompt
    assert "Do not emit routine host resource screens" in prompt
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

    assert frames == [("ERR:scheduler ra", "ised unexpected ")]
    assert len(frames[0][0]) == 16
    assert len(frames[0][1]) == 16


def test_normalize_screens_preserves_existing_inline_header() -> None:
    frames = services.normalize_screens([("ERR:svc fail", "manual rst")])

    assert frames == [("ERR:svc fail    ", "manual rst      ")]


def test_normalize_screens_preserves_prewrapped_inline_buffer() -> None:
    frames = services.normalize_screens([("ERR2 WRN1:Panic", "failure")])

    assert frames == [("ERR2 WRN1:Panic ", "failure         ")]


def test_filter_redundant_lcd_summary_screens_handles_inline_headers() -> None:
    frames = services.filter_redundant_lcd_summary_screens(
        [
            ("HOST:t65C d51% m44%", ""),
            ("ERR:svc failed", "manual"),
        ]
    )

    assert frames == [("ERR:svc failed", "manual")]


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


def test_legacy_low_summary_frames_are_removed(tmp_path) -> None:
    (tmp_path / "lcd-low").write_text("old\nsummary\n", encoding="utf-8")
    (tmp_path / "lcd-low-1").write_text("old\nsummary\n", encoding="utf-8")
    (tmp_path / "lcd-low-2").write_text("old\nsummary\n", encoding="utf-8")
    (tmp_path / "lcd-low-extra").write_text("keep\nme\n", encoding="utf-8")

    services.clear_legacy_low_summary_frames(tmp_path)

    assert not (tmp_path / "lcd-low").exists()
    assert not (tmp_path / "lcd-low-1").exists()
    assert not (tmp_path / "lcd-low-2").exists()
    assert (tmp_path / "lcd-low-extra").exists()


def test_no_log_generation_removes_legacy_low_summary_frames(
    monkeypatch, settings, tmp_path
) -> None:
    from apps.nodes.models import Node

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd-low").write_text("old\nsummary\n", encoding="utf-8")
    (lock_dir / "lcd-low-1").write_text("old\nsummary\n", encoding="utf-8")

    class FakeNode:
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
    assert not (lock_dir / "lcd-low").exists()
    assert not (lock_dir / "lcd-low-1").exists()


def test_suite_gate_skip_removes_legacy_low_summary_frames(
    monkeypatch, settings, tmp_path
) -> None:
    from apps.nodes.models import Node

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd-low").write_text("old\nsummary\n", encoding="utf-8")
    (lock_dir / "lcd-low-1").write_text("old\nsummary\n", encoding="utf-8")

    settings.BASE_DIR = tmp_path
    monkeypatch.setattr(Node, "get_local", staticmethod(lambda: object()))
    monkeypatch.setattr(
        services,
        "is_suite_feature_enabled",
        lambda slug, default=True: False,
    )

    result = services.execute_log_summary_generation()

    assert result == "skipped:suite-feature-disabled"
    assert not (lock_dir / "lcd-low").exists()
    assert not (lock_dir / "lcd-low-1").exists()
