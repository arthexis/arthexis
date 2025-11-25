import os
import subprocess
import sys
from pathlib import Path


def test_suite_can_hydrate_from_pypi_install(tmp_path: Path) -> None:
    install_dir = tmp_path / "site"
    env = os.environ.copy()

    install_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-build-isolation",
        "--target",
        str(install_dir),
        ".",
    ]
    subprocess.run(install_cmd, check=True, env=env)

    hydration_script = f"""
import os
import sys
from pathlib import Path

sys.path.insert(0, "{install_dir}")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
from django.core.management import call_command

django.setup()
call_command("migrate", run_syncdb=True, verbosity=0)
from core.user_data import load_shared_user_fixtures

load_shared_user_fixtures(force=True)
print("hydrated")
"""

    result = subprocess.run(
        [sys.executable, "-c", hydration_script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "hydrated" in result.stdout
