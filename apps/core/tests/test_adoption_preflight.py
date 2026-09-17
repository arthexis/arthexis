import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.core.system import adoption, managed_install
from apps.core.system.lifecycle import InstallationLayout


def _source(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "manage.py").write_text("# source checkout\n", encoding="utf-8")
    (source / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    locks = source / ".locks"
    locks.mkdir()
    (locks / "role.lck").write_text("Control\n", encoding="utf-8")
    return source


class AdoptionPreflightTests(SimpleTestCase):
    def test_preflight_inventory_is_read_only_and_hides_env_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            database = source / "db.sqlite3"
            database.write_text("developer-state", encoding="utf-8")
            (source / ".venv").mkdir()
            (source / "media").mkdir()
            secret = "DO_NOT_PRINT_THIS_SECRET"
            (source / "production.env").write_text(
                f"TOKEN={secret}\n", encoding="utf-8"
            )
            target = root / "managed"

            with mock.patch.object(
                adoption,
                "_git_inventory",
                return_value=("abc123", "feature", True),
            ):
                plan = adoption.inspect_adoption(source, target_root=target)

            self.assertTrue(plan["ready"])
            self.assertEqual(plan["source"], str(source.resolve()))
            self.assertEqual(plan["target_checkout"], str((target / "app").resolve()))
            self.assertEqual(plan["revision"], "abc123")
            self.assertEqual(plan["branch"], "feature")
            self.assertIs(plan["dirty"], True)
            self.assertEqual(plan["version"], "1.2.3")
            self.assertEqual(plan["role"], "Control")
            self.assertEqual(plan["environment_files"], ["production.env"])
            self.assertNotIn(secret, repr(plan))
            self.assertEqual(database.read_text(encoding="utf-8"), "developer-state")
            self.assertFalse(target.exists())

            by_kind = {item["kind"]: item for item in plan["transfers"]}
            self.assertEqual(by_kind["database"]["classification"], "sqlite-backup")
            self.assertEqual(
                by_kind["python-environment"]["classification"], "regenerable"
            )
            self.assertEqual(by_kind["media"]["classification"], "copyable")
            self.assertEqual(by_kind["services"]["classification"], "regenerable")

    def test_preflight_uses_sqlite_backup_when_sidecars_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            (source / "db.sqlite3").write_text("database", encoding="utf-8")
            wal = source / "db.sqlite3-wal"
            shm = source / "db.sqlite3-shm"
            wal.write_text("wal", encoding="utf-8")
            shm.write_text("shm", encoding="utf-8")

            with mock.patch.object(
                adoption,
                "_git_inventory",
                return_value=("abc123", "main", False),
            ):
                plan = adoption.inspect_adoption(
                    source, target_root=root / "managed"
                )

            database = next(
                item for item in plan["transfers"] if item["kind"] == "database"
            )
            self.assertEqual(database["classification"], "sqlite-backup")
            self.assertEqual(plan["sqlite_sidecars"], [str(wal), str(shm)])
            self.assertIn("consistent SQLite backup", database["detail"])

    def test_preflight_reports_existing_managed_target_as_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            target = root / "managed"
            (target / "app").mkdir(parents=True)

            with mock.patch.object(
                adoption,
                "_git_inventory",
                return_value=("abc123", "main", False),
            ):
                plan = adoption.inspect_adoption(source, target_root=target)

            self.assertFalse(plan["ready"])
            self.assertTrue(
                any(
                    "target checkout already exists" in blocker
                    for blocker in plan["blockers"]
                )
            )

    def test_git_inspection_disables_fsmonitor_and_optional_locks(self) -> None:
        completed = SimpleNamespace(stdout="clean\n")
        with mock.patch.object(adoption.subprocess, "run", return_value=completed) as run:
            result = adoption._git(Path("/tmp/source"), "status", "--porcelain=v1")

        self.assertEqual(result, "clean")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["git", "-c", "core.fsmonitor=false", "--no-optional-locks"])
        self.assertIn("-C", command)

    def test_git_inventory_does_not_execute_configured_fsmonitor(self) -> None:
        if os.name != "posix" or shutil.which("git") is None:
            self.skipTest("requires POSIX git")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Test"],
                check=True,
            )
            tracked = source / "tracked.txt"
            tracked.write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(source), "commit", "-q", "-m", "fixture"],
                check=True,
            )

            marker = root / "fsmonitor-ran"
            monitor = root / "fsmonitor.sh"
            monitor.write_text(
                f"#!/bin/sh\ntouch '{marker}'\nexit 0\n",
                encoding="utf-8",
            )
            monitor.chmod(0o755)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source),
                    "config",
                    "core.fsmonitor",
                    str(monitor),
                ],
                check=True,
            )

            blockers: list[str] = []
            revision, branch, dirty = adoption._git_inventory(source, blockers)

            self.assertTrue(revision)
            self.assertIsNotNone(branch)
            self.assertIs(dirty, False)
            self.assertEqual(blockers, [])
            self.assertFalse(marker.exists())

    def test_managed_install_routes_adoption_dry_run_to_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            current = InstallationLayout(
                root=root / "managed", checkout=root / "managed" / "app"
            )
            expected = {"mode": "adoption-preflight", "ready": True}

            with mock.patch.object(
                managed_install, "inspect_adoption", return_value=expected
            ) as inspect:
                result = managed_install.install(
                    "--adopt",
                    "--from",
                    str(source),
                    "--dry-run",
                    layout=current,
                )

            self.assertIs(result, expected)
            inspect.assert_called_once_with(str(source), target_root=current.root)
            self.assertFalse(current.root.exists())

    def test_managed_install_preserves_normal_install_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = InstallationLayout(root=root, checkout=root / "app")
            expected = object()

            with mock.patch.object(
                managed_install.lifecycle, "install", return_value=expected
            ) as install:
                result = managed_install.install(
                    "--role", "Terminal", layout=current
                )

            self.assertIs(result, expected)
            install.assert_called_once_with("--role", "Terminal", layout=current)

    def test_managed_install_refuses_mutating_adoption_without_gway_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = _source(Path(temporary))
            with self.assertRaisesRegex(
                managed_install.AdoptionArgumentError, "not implemented"
            ):
                managed_install.install("--adopt", "--from", str(source))

    def test_malformed_adoption_option_uses_adoption_error_contract(self) -> None:
        with self.assertRaises(managed_install.AdoptionArgumentError):
            managed_install.install("--adopt", "--from", "--dry-run")
