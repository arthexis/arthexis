import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def clone_repo(tmp_path: Path) -> Path:
    clone_dir = tmp_path / "repo"
    shutil.copytree(REPO_ROOT, clone_dir)
    return clone_dir


def build_incompatible_wheel(repo: Path) -> str:
    """Create a local wheel that is incompatible with the current interpreter."""

    wheel_dir = repo / "local-packages"
    wheel_dir.mkdir(exist_ok=True)
    package_dir = wheel_dir / "checkdep"
    package_dir.mkdir(exist_ok=True)
    (package_dir / "__init__.py").write_text("__version__ = '0.1'\n")

    dist_info = wheel_dir / "checkdep-0.1.dist-info"
    dist_info.mkdir(exist_ok=True)

    major, minor = sys.version_info[:2]
    requires_python = f"<{major}.{minor}"

    metadata = """Metadata-Version: 2.1
Name: checkdep
Version: 0.1
Summary: Compatibility sentinel for env-refresh
Requires-Python: {requires_python}
""".format(
        requires_python=requires_python
    )

    (dist_info / "METADATA").write_text(metadata)
    (dist_info / "WHEEL").write_text(
        """Wheel-Version: 1.0
Generator: manual
Root-Is-Purelib: true
Tag: py3-none-any
"""
    )
    (dist_info / "RECORD").write_text("")

    wheel_path = wheel_dir / "checkdep-0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        archive.write(package_dir / "__init__.py", "checkdep/__init__.py")
        archive.write(dist_info / "METADATA", "checkdep-0.1.dist-info/METADATA")
        archive.write(dist_info / "WHEEL", "checkdep-0.1.dist-info/WHEEL")
        archive.write(dist_info / "RECORD", "checkdep-0.1.dist-info/RECORD")

    return str(wheel_path.relative_to(repo))


def test_env_refresh_reports_incompatible_dependency(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    wheel_requirement = build_incompatible_wheel(repo)

    requirements = repo / "requirements.txt"
    requirements.write_text(f"{wheel_requirement}\n")

    for md5_name in ("requirements.md5", "requirements.system.md5"):
        md5_path = repo / md5_name
        if md5_path.exists():
            md5_path.unlink()

    result = subprocess.run(
        ["bash", "env-refresh.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    combined_output = f"{result.stdout}\n{result.stderr}"
    assert "requires a different Python" in combined_output
