from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


@dataclass(frozen=True)
class MigrationSnapshot:
    """Represent cumulative migration size metrics for one reporting period."""

    period: str
    new_files: int
    new_bytes: int
    total_files: int
    total_bytes: int


class Command(BaseCommand):
    """Estimate how migration file size grows over time."""

    help = (
        "Estimate migration growth over time by grouping migration file creation "
        "dates into monthly or yearly snapshots."
    )

    def add_arguments(self, parser) -> None:
        """Register command options."""

        parser.add_argument(
            "--apps-dir",
            type=Path,
            default=Path(getattr(settings, "APPS_DIR", Path(settings.BASE_DIR) / "apps")),
            help="Directory that contains Django apps (default: settings.APPS_DIR).",
        )
        parser.add_argument(
            "--group-by",
            choices=("month", "year"),
            default="month",
            help="Time period used to group migration growth data.",
        )
        parser.add_argument(
            "--format",
            choices=("table", "json"),
            default="table",
            help="Output format.",
        )

    def handle(self, *args, **options) -> None:
        """Render migration growth estimates in the requested format."""

        apps_dir = Path(options["apps_dir"])
        if not apps_dir.exists():
            raise CommandError(f"Apps directory does not exist: {apps_dir}")

        migration_files = self._collect_migration_files(apps_dir)
        if not migration_files:
            raise CommandError(f"No migration files found in {apps_dir}")

        file_dates = self._resolve_file_dates(migration_files=migration_files, apps_dir=apps_dir)
        snapshots = self._build_snapshots(
            migration_files=migration_files,
            file_dates=file_dates,
            group_by=options["group_by"],
        )

        if options["format"] == "json":
            self.stdout.write(json.dumps([asdict(snapshot) for snapshot in snapshots], indent=2))
            return

        self._write_table(snapshots)

    def _collect_migration_files(self, apps_dir: Path) -> list[Path]:
        """Return migration Python files under ``apps_dir`` except package markers."""

        return sorted(
            path
            for path in apps_dir.glob("*/migrations/*.py")
            if path.name != "__init__.py"
        )

    def _resolve_file_dates(self, migration_files: list[Path], apps_dir: Path) -> dict[Path, datetime]:
        """Resolve creation timestamps for migration files from git history."""

        repo_root = Path(settings.BASE_DIR)
        git_dates = self._load_git_created_dates(repo_root=repo_root, apps_dir=apps_dir)
        file_dates: dict[Path, datetime] = {}

        for migration_path in migration_files:
            relative = migration_path.resolve().relative_to(apps_dir.resolve()).as_posix()
            if relative in git_dates:
                file_dates[migration_path] = git_dates[relative]
                continue

            modified = datetime.fromtimestamp(migration_path.stat().st_mtime, tz=UTC)
            file_dates[migration_path] = modified
            self.stderr.write(
                self.style.WARNING(
                    f"Missing git creation date for {relative}; using file mtime instead."
                )
            )

        return file_dates

    def _load_git_created_dates(self, repo_root: Path, apps_dir: Path) -> dict[str, datetime]:
        """Load migration creation dates from git ``--diff-filter=A`` history."""

        resolved_apps_dir = apps_dir.resolve()
        resolved_repo_root = repo_root.resolve()
        try:
            apps_relative = resolved_apps_dir.relative_to(resolved_repo_root).as_posix()
        except ValueError:
            return {}

        command = [
            "git",
            "log",
            "--diff-filter=A",
            "--format=%aI",
            "--name-only",
            "--",
            apps_relative,
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                cwd=repo_root,
            )
        except subprocess.CalledProcessError as exc:
            self.stderr.write(
                self.style.WARNING(
                    "Unable to inspect git history; using file mtime for migration dates. "
                    f"git log failed with: {exc.stderr.strip() or exc}"
                )
            )
            return {}

        creation_dates: dict[str, datetime] = {}
        current_date: datetime | None = None

        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                current_date = datetime.fromisoformat(line.replace("Z", "+00:00"))
                continue
            except ValueError:
                pass
            if current_date is None:
                continue
            if not line.endswith(".py") or "/migrations/" not in line or line.endswith("/__init__.py"):
                continue
            try:
                relative_to_apps = (resolved_repo_root / line).resolve().relative_to(resolved_apps_dir).as_posix()
            except ValueError:
                continue
            creation_dates.setdefault(relative_to_apps, current_date)

        return creation_dates

    def _build_snapshots(
        self,
        migration_files: list[Path],
        file_dates: dict[Path, datetime],
        group_by: str,
    ) -> list[MigrationSnapshot]:
        """Build per-period and cumulative migration size snapshots."""

        grouped_sizes: dict[str, list[int]] = defaultdict(list)
        for path in migration_files:
            created_at = file_dates[path]
            period = created_at.strftime("%Y-%m") if group_by == "month" else created_at.strftime("%Y")
            grouped_sizes[period].append(path.stat().st_size)

        snapshots: list[MigrationSnapshot] = []
        total_files = 0
        total_bytes = 0
        for period in sorted(grouped_sizes):
            sizes = grouped_sizes[period]
            new_files = len(sizes)
            new_bytes = sum(sizes)
            total_files += new_files
            total_bytes += new_bytes
            snapshots.append(
                MigrationSnapshot(
                    period=period,
                    new_files=new_files,
                    new_bytes=new_bytes,
                    total_files=total_files,
                    total_bytes=total_bytes,
                )
            )
        return snapshots

    def _write_table(self, snapshots: list[MigrationSnapshot]) -> None:
        """Write a plain-text table and aggregate growth summary."""

        header = f"{'Period':<10} {'New files':>10} {'New bytes':>12} {'Total files':>12} {'Total bytes':>12}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for row in snapshots:
            self.stdout.write(
                f"{row.period:<10} {row.new_files:>10} {row.new_bytes:>12} {row.total_files:>12} {row.total_bytes:>12}"
            )

        if len(snapshots) >= 2:
            periods = len(snapshots) - 1
            avg_growth = (snapshots[-1].total_bytes - snapshots[0].total_bytes) / periods
            self.stdout.write(f"\nAverage cumulative growth per period: {avg_growth:.2f} bytes")
