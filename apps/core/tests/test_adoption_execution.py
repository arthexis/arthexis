import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from apps.core.system import adoption, managed_install
from apps.core.system.lifecycle import InstallationLayout


def _source(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "manage.py").write_text("# source checkout\n", encoding="utf-8")
    (source / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    with sqlite3.connect(source / "db.sqlite3") as database:
        database.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        database.execute("INSERT INTO sample (value) VALUES ('developer-db')")
    (source / "media").mkdir()
    (source / "media" / "asset.txt").write_text("asset", encoding="utf-8")
    (source / "production.env").write_text("TOKEN=secret\n", encoding="utf-8")
    locks = source / ".locks"
    locks.mkdir()
    (locks / "role.lck").write_text("Control\n", encoding="utf-8")
    return source


def _managed(root: Path) -> InstallationLayout:
    managed = root / "managed"
    checkout = managed / "app"
    environment = managed / ".venv"
    checkout.mkdir(parents=True)
    environment.mkdir()
    return InstallationLayout(root=managed, checkout=checkout)


def _sample_values(database_path: Path) -> list[str]:
    with sqlite3.connect(database_path) as database:
        return [row[0] for row in database.execute("SELECT value FROM sample ORDER BY rowid")]


class AdoptionExecutionTests(SimpleTestCase):
    def test_execution_transfers_durable_state_and_records_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            layout = _managed(root)

            with mock.patch.object(
                adoption,
                "_git_inventory",
                return_value=("abc123", "feature", False),
            ):
                plan = adoption.execute_adoption(source, target_root=layout.root)

            data = layout.root / "var" / "lib"
            self.assertEqual(_sample_values(data / "db.sqlite3"), ["developer-db"])
            self.assertEqual((data / "media" / "asset.txt").read_text(), "asset")
            self.assertEqual(
                (data / "config" / "production.env").read_text(), "TOKEN=secret\n"
            )
            provenance = json.loads((data / "adoption.json").read_text())
            self.assertEqual(provenance["revision"], "abc123")
            self.assertEqual(provenance["version"], "1.2.3")
            self.assertEqual(provenance["role"], "Control")
            self.assertFalse(provenance["dirty_source_acknowledged"])
            self.assertEqual(_sample_values(source / "db.sqlite3"), ["developer-db"])
            self.assertEqual(plan["source"], str(source.resolve()))

    def test_execution_refuses_dirty_source_without_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            layout = _managed(root)

            with mock.patch.object(
                adoption,
                "_git_inventory",
                return_value=("abc123", "feature", True),
            ):
                with self.assertRaisesRegex(
                    adoption.AdoptionExecutionError, "--allow-dirty-source"
                ):
                    adoption.execute_adoption(source, target_root=layout.root)

            self.assertFalse((layout.root / "var" / "lib" / "adoption.json").exists())

    def test_execution_snapshots_sqlite_with_live_wal_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            layout = _managed(root)
            database_path = source / "db.sqlite3"
            writer = sqlite3.connect(database_path)
            self.addCleanup(writer.close)
            writer.execute("PRAGMA journal_mode=WAL")
            original_snapshot = adoption._snapshot_sqlite

            def snapshot_after_live_write(source_path: Path, target_path: Path) -> None:
                writer.execute("INSERT INTO sample (value) VALUES ('live-write')")
                writer.commit()
                original_snapshot(source_path, target_path)

            with (
                mock.patch.object(
                    adoption,
                    "_git_inventory",
                    return_value=("abc123", "main", False),
                ),
                mock.patch.object(
                    adoption, "_snapshot_sqlite", side_effect=snapshot_after_live_write
                ),
            ):
                adoption.execute_adoption(source, target_root=layout.root)

            installed = layout.root / "var" / "lib" / "db.sqlite3"
            self.assertEqual(
                _sample_values(installed), ["developer-db", "live-write"]
            )
            with sqlite3.connect(installed) as database:
                self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_execution_refuses_symlinked_transfer_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (source / "media" / "escape").symlink_to(outside)
            layout = _managed(root)

            with mock.patch.object(
                adoption,
                "_git_inventory",
                return_value=("abc123", "main", False),
            ):
                with self.assertRaisesRegex(
                    adoption.AdoptionExecutionError, "contains a symlink"
                ):
                    adoption.execute_adoption(source, target_root=layout.root)

            self.assertFalse((layout.root / "var" / "lib" / "adoption.json").exists())

    def test_execution_rolls_back_if_interrupted_after_staged_move(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            layout = _managed(root)
            original_replace = Path.replace
            moves = 0

            def interrupt_after_move(path: Path, target: Path) -> Path:
                nonlocal moves
                result = original_replace(path, target)
                moves += 1
                if moves == 2:
                    raise KeyboardInterrupt
                return result

            with (
                mock.patch.object(
                    adoption,
                    "_git_inventory",
                    return_value=("abc123", "main", False),
                ),
                mock.patch.object(Path, "replace", autospec=True, side_effect=interrupt_after_move),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    adoption.execute_adoption(source, target_root=layout.root)

            target_data = layout.root / "var" / "lib"
            self.assertTrue(target_data.exists())
            self.assertEqual(list(target_data.iterdir()), [])

    def test_managed_install_executes_transfer_then_normal_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            layout = _managed(root)
            plan = {
                "role": "Control",
                "target_persistent_data": str(layout.root / "var" / "lib"),
            }
            expected = object()

            with (
                mock.patch.object(
                    managed_install, "execute_adoption", return_value=plan
                ) as execute,
                mock.patch.object(
                    managed_install.lifecycle, "install", return_value=expected
                ) as install,
            ):
                result = managed_install.install(
                    "--adopt", "--from", str(source), layout=layout
                )

            self.assertIs(result, expected)
            execute.assert_called_once_with(
                str(source), target_root=layout.root, allow_dirty_source=False
            )
            install.assert_called_once_with("--role", "Control", layout=layout)

    def test_managed_install_rolls_back_transfer_when_lifecycle_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            layout = _managed(root)
            plan = {
                "role": "Control",
                "target_persistent_data": str(layout.root / "var" / "lib"),
            }

            with (
                mock.patch.object(
                    managed_install, "execute_adoption", return_value=plan
                ),
                mock.patch.object(
                    managed_install.lifecycle,
                    "install",
                    side_effect=RuntimeError("prepare failed"),
                ),
                mock.patch.object(managed_install, "rollback_adoption") as rollback,
            ):
                with self.assertRaisesRegex(RuntimeError, "prepare failed"):
                    managed_install.install(
                        "--adopt", "--from", str(source), layout=layout
                    )

            rollback.assert_called_once_with(plan)

    def test_managed_install_rolls_back_transfer_when_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            layout = _managed(root)
            plan = {
                "role": "Control",
                "target_persistent_data": str(layout.root / "var" / "lib"),
            }

            with (
                mock.patch.object(
                    managed_install, "execute_adoption", return_value=plan
                ),
                mock.patch.object(
                    managed_install.lifecycle,
                    "install",
                    side_effect=KeyboardInterrupt,
                ),
                mock.patch.object(managed_install, "rollback_adoption") as rollback,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    managed_install.install(
                        "--adopt", "--from", str(source), layout=layout
                    )

            rollback.assert_called_once_with(plan)

    def test_rollback_removes_only_an_adoption_owned_data_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "var" / "lib"
            data.mkdir(parents=True)
            (data / "adoption.json").write_text("{}\n", encoding="utf-8")
            (data / "db.sqlite3").write_text("partial", encoding="utf-8")
            (data / "media").mkdir()
            plan = {"target_persistent_data": str(data)}

            adoption.rollback_adoption(plan)

            self.assertTrue(data.exists())
            self.assertEqual(list(data.iterdir()), [])

    def test_dirty_source_acknowledgement_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source(root)
            layout = _managed(root)
            plan = {
                "role": "Control",
                "target_persistent_data": str(layout.root / "var" / "lib"),
            }

            with (
                mock.patch.object(
                    managed_install, "execute_adoption", return_value=plan
                ) as execute,
                mock.patch.object(managed_install.lifecycle, "install"),
            ):
                managed_install.install(
                    "--adopt",
                    "--from",
                    str(source),
                    "--allow-dirty-source",
                    layout=layout,
                )

            execute.assert_called_once_with(
                str(source), target_root=layout.root, allow_dirty_source=True
            )
