from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from utils import service_probe


def test_detect_runserver_port_prefers_matching_base_dir(monkeypatch, tmp_path: Path):
    target_base_dir = tmp_path / "arthexis"
    other_base_dir = tmp_path / "porsche"
    target_base_dir.mkdir()
    other_base_dir.mkdir()

    monkeypatch.setattr(
        service_probe.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "1001 python manage.py runserver 0.0.0.0:8889 --noreload\n"
                "1002 python manage.py runserver 0.0.0.0:8888 --noreload\n"
            ),
        ),
    )
    monkeypatch.setattr(
        service_probe,
        "_process_cwd_matches_base_dir",
        lambda pid, base_dir: {
            1001: other_base_dir.resolve(),
            1002: target_base_dir.resolve(),
        }[pid]
        == base_dir,
    )

    detected_port = service_probe.detect_runserver_port(target_base_dir)

    assert detected_port == 8888


def test_detect_runserver_port_returns_none_when_no_process_matches_base_dir(
    monkeypatch, tmp_path: Path
):
    target_base_dir = tmp_path / "arthexis"
    target_base_dir.mkdir()

    monkeypatch.setattr(
        service_probe.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="1001 python manage.py runserver 0.0.0.0:8889 --noreload\n",
        ),
    )
    monkeypatch.setattr(
        service_probe,
        "_process_cwd_matches_base_dir",
        lambda pid, base_dir: False,
    )

    detected_port = service_probe.detect_runserver_port(target_base_dir)

    assert detected_port is None


def test_detect_runserver_port_returns_none_when_base_dir_is_missing(
    monkeypatch, tmp_path: Path
):
    missing_base_dir = tmp_path / "does-not-exist"

    monkeypatch.setattr(
        service_probe.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="999999 python manage.py runserver 0.0.0.0:8889 --noreload\n",
        ),
    )

    detected_port = service_probe.detect_runserver_port(missing_base_dir)

    assert detected_port is None


def test_process_cwd_matches_base_dir_handles_missing_proc_entry():
    assert (
        service_probe._process_cwd_matches_base_dir(999999, Path("/tmp")) is False
    )


def test_main_detect_runserver_port_accepts_base_dir(monkeypatch, tmp_path: Path, capsys):
    target_base_dir = tmp_path / "arthexis"
    target_base_dir.mkdir()

    monkeypatch.setattr(service_probe, "detect_runserver_port", lambda base_dir=None: 8888)

    exit_code = service_probe.main(
        ["detect-runserver-port", "--base-dir", str(target_base_dir)]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "8888"
