import io
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from apps.core.management.commands.estimate_migration_growth import Command


class EstimateMigrationGrowthCommandTests(SimpleTestCase):
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
                first: datetime(2024, 1, 1, tzinfo=UTC),
                second: datetime(2024, 2, 1, tzinfo=UTC),
            }

            stdout = io.StringIO()
            with patch.object(Command, "_resolve_file_dates", return_value=file_dates):
                call_command(
                    "estimate_migration_growth",
                    apps_dir=str(apps_dir),
                    format="json",
                    stdout=stdout,
                )

            output = stdout.getvalue()
            assert '"period": "2024-01"' in output
            assert '"total_bytes": 10' in output
            assert '"period": "2024-02"' in output
            assert '"total_bytes": 35' in output

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

            with patch("apps.core.management.commands.estimate_migration_growth.settings.BASE_DIR", repo_root):
                with patch.object(command, "_load_git_created_dates", return_value={}):
                    file_dates = command._resolve_file_dates([migration_file])

            assert migration_file in file_dates
            assert "using file mtime instead" in command.stderr.getvalue()
