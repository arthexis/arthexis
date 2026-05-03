from datetime import datetime, timezone
import subprocess

from apps.summary import services
from apps.tasks.tasks import _write_lcd_frames


def test_fixed_frame_window_does_not_pad_blank_frames() -> None:
    frames = services.fixed_frame_window([("A", "B"), ("C", "D")])

    assert frames == [("A", "B"), ("C", "D")]


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
    monkeypatch.setattr(services, "collect_noteworthy_status_lines", lambda: [])

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


def test_collect_noteworthy_status_lines_adds_host_fallback_sources(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        if name in {"journalctl", "systemctl"}:
            return f"/usr/bin/{name}"
        return None

    def fake_run(args: list[str]):
        if "systemctl" in args[0]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "journalctl" in args[0]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="\n".join(
                    [
                        "2026-05-03T08:32:27-0600 host kernel: FAT-fs (sda1): "
                        "Directory bread(block 32768) failed",
                        "2026-05-03T08:32:46-0600 host kernel: FAT-fs (sda1): "
                        "FAT read failed (blocknr 1986)",
                        "2026-05-03T09:59:22-0600 host sshd[1]: error: "
                        "kex_exchange_identification: Connection closed by remote host",
                    ]
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(services.shutil, "which", fake_which)
    monkeypatch.setattr(services, "_run_status_command", fake_run)
    monkeypatch.setattr(
        services,
        "_read_usb_inventory",
        lambda: {
            "devices": [
                {
                    "claimed_roles": ["bastion-unlock"],
                    "label": "ESD-USB",
                    "mounts": [{"read_only": True}],
                    "name": "sda1",
                    "transport": "usb",
                    "type": "part",
                }
            ]
        },
    )
    monkeypatch.setattr(
        services,
        "_host_resource_status_line",
        lambda: "OK host: t62C d54% m46%",
    )

    lines = services.collect_noteworthy_status_lines()

    assert "OK status: 0 failed units" in lines
    assert "ERR journal: USB FAT sda1 x2 last 08:32" in lines
    assert "OK usb: sda1 ro bastion" in lines
    assert "OK host: t62C d54% m46%" in lines
    assert not any("kex_exchange" in line for line in lines)
