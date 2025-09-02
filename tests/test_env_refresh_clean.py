import subprocess
import importlib.util
from pathlib import Path

from django.core.management import call_command
from django.conf import settings


def test_env_refresh_leaves_repo_clean():
    base_dir = Path(settings.BASE_DIR)

    spec = importlib.util.spec_from_file_location("env_refresh", base_dir / "env-refresh.py")
    env_refresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env_refresh)

    local_apps = env_refresh._local_app_labels()

    # Ensure no new migrations are needed
    call_command("makemigrations", *local_apps, interactive=False, check=True)

    # Confirm repository is clean
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=base_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""

