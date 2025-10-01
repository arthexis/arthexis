import types
from core import release


def test_run_tests_writes_log(monkeypatch, tmp_path):
    log_file = tmp_path / "out.log"

    def fake_run(cmd, capture_output, text):
        return types.SimpleNamespace(returncode=1, stdout="out", stderr="err")

    monkeypatch.setattr(release.subprocess, "run", fake_run)

    proc = release.run_tests(log_path=log_file)
    assert proc.returncode == 1
    assert log_file.read_text() == "outerr"


def test_run_tests_uses_custom_command(monkeypatch, tmp_path):
    log_file = tmp_path / "out.log"
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="out", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_run)

    proc = release.run_tests(log_path=log_file, command=["pytest", "-k", "smoke"])
    assert proc.returncode == 0
    assert calls == [["pytest", "-k", "smoke"]]
