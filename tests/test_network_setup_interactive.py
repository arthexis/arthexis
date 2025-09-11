from pathlib import Path
import subprocess

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_network_setup_help_includes_interactive() -> None:
    script = REPO_ROOT / "network-setup.sh"
    result = subprocess.run(
        [
            "bash",
            str(script),
            "--help",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    assert "--interactive" in result.stdout
