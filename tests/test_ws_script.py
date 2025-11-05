from pathlib import Path
import stat


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "ws.sh"


def test_ws_script_exists_and_executable() -> None:
    assert SCRIPT_PATH.exists(), "ws.sh should exist in the repository root"
    mode = SCRIPT_PATH.stat().st_mode
    assert mode & stat.S_IXUSR, "ws.sh must be executable by the owner"


def test_ws_script_contains_expected_guards() -> None:
    content = SCRIPT_PATH.read_text()
    assert "SESSION_NAME=\"main\"" in content
    assert "case $-" in content
    assert "screen -xRR \"$SESSION_NAME\"" in content
