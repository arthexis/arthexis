import io
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from apps.core.management.commands.estimate import Command


class EstimateCommandTests(SimpleTestCase):
    def test_reports_cumulative_growth_as_json(self):
        with TemporaryDirectory() as tmpdir:
            apps_dir = Path(tmpdir) / "apps"
            migrations_dir = apps_dir / "sample" / "migrations"
            migrations_dir.mkdir(parents=True)

            first = migrations_dir / "0001_initial.py"
            second = migrations_dir / "0002_add_name.py"
            first.write_text("a" * 10, encoding="utf-8")
            second.write_text("b" * 25, encoding="utf-8")

            file_dates = {
                first: datetime(2024, 1, 1, tzinfo=timezone.utc),
                second: datetime(2024, 2, 1, tzinfo=timezone.utc),
            }

            stdout = io.StringIO()
            with patch.object(Command, "_resolve_file_dates", return_value=file_dates):
                call_command(
                    "estimate",
                    apps_dir=str(apps_dir),
                    format="json",
                    stdout=stdout,
                )

            parsed_output = json.loads(stdout.getvalue())
            assert len(parsed_output) == 2
            assert parsed_output[0]["period"] == "2024-01"
            assert parsed_output[0]["total_bytes"] == 10
            assert parsed_output[1]["period"] == "2024-02"
            assert parsed_output[1]["total_bytes"] == 35

    def test_warns_and_uses_mtime_when_git_date_missing(self):
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            apps_dir = repo_root / "apps"
            migrations_dir = apps_dir / "sample" / "migrations"
            migrations_dir.mkdir(parents=True)
            migration_file = migrations_dir / "0001_initial.py"
            migration_file.write_text("content", encoding="utf-8")

            command = Command()
            command.stdout = io.StringIO()
            command.stderr = io.StringIO()

            with patch("apps.core.management.commands.estimate.settings.BASE_DIR", repo_root):
                with patch.object(command, "_load_git_created_dates", return_value={}):
                    file_dates = command._resolve_file_dates([migration_file], apps_dir=apps_dir)

            assert migration_file in file_dates
            assert "using file mtime instead" in command.stderr.getvalue()

    def test_uses_mtime_when_apps_dir_outside_repo_root(self):
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            external_apps = Path(tmpdir) / "external_apps"
            repo_root.mkdir()
            external_apps.mkdir()

            command = Command()
            command.stderr = io.StringIO()

            with patch("apps.core.management.commands.estimate.settings.BASE_DIR", repo_root):
                git_dates = command._load_git_created_dates(repo_root=repo_root, apps_dir=external_apps)

            assert git_dates == {}

    def test_warns_and_returns_empty_when_git_log_fails(self):
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            apps_dir = repo_root / "apps"
            apps_dir.mkdir()

            command = Command()
            command.stderr = io.StringIO()

            error = subprocess.CalledProcessError(
                returncode=1,
                cmd=["git", "log"],
                stderr="shallow update not allowed",
            )
            with patch("subprocess.run", side_effect=error):
                git_dates = command._load_git_created_dates(repo_root=repo_root, apps_dir=apps_dir)

            assert git_dates == {}
            assert "Unable to inspect git history" in command.stderr.getvalue()
