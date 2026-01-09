import hashlib
import importlib.util
from pathlib import Path

import pytest
from django.conf import settings


@pytest.fixture(scope="session")
def env_refresh_module():
    path = Path(settings.BASE_DIR) / "env-refresh.py"
    spec = importlib.util.spec_from_file_location("env_refresh", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_fixture(base_dir: Path, relative: str, content: str) -> str:
    path = base_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return relative


def test_migration_hash_reads_migration_files(tmp_path, monkeypatch, env_refresh_module):
    app_one = tmp_path / "apps" / "one"
    app_two = tmp_path / "apps" / "two"
    (app_one / "migrations").mkdir(parents=True)
    (app_two / "migrations").mkdir(parents=True)

    migration_one = app_one / "migrations" / "0001_initial.py"
    migration_one.write_text("initial migration")
    migration_two = app_two / "migrations" / "0002_add_field.py"
    migration_two.write_text("add field")
    (app_two / "migrations" / "__init__.py").write_text("")

    class StubConfig:
        def __init__(self, path: Path) -> None:
            self.path = str(path)

    configs = {"app_one": StubConfig(app_one), "app_two": StubConfig(app_two)}

    def get_app_config(label: str):
        return configs[label]

    monkeypatch.setattr(env_refresh_module.apps, "get_app_config", get_app_config)

    digest = hashlib.md5(usedforsecurity=False)
    digest.update(migration_one.read_bytes())
    digest.update(migration_two.read_bytes())
    expected = digest.hexdigest()

    assert env_refresh_module._migration_hash(["app_one", "app_two"]) == expected


def test_fixtures_hash_uses_relative_paths(tmp_path, monkeypatch, env_refresh_module):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    fixtures = [
        _write_fixture(tmp_path, "fixtures/global.json", '{"a": 1}'),
        _write_fixture(tmp_path, "apps/sample/fixtures/seed.json", '{"b": 2}'),
        "fixtures/missing.json",
    ]

    digest = hashlib.md5(usedforsecurity=False)
    for fixture in sorted(fixtures):
        path = tmp_path / fixture
        try:
            digest.update(str(path.relative_to(tmp_path)).encode("utf-8"))
            digest.update(path.read_bytes())
        except OSError:
            continue

    assert env_refresh_module._fixtures_hash(fixtures) == digest.hexdigest()


def test_fixture_hashes_group_by_app(tmp_path, monkeypatch, env_refresh_module):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    fixtures = [
        _write_fixture(tmp_path, "fixtures/global.json", '{"a": 1}'),
        _write_fixture(tmp_path, "apps/alpha/fixtures/alpha.json", '{"b": 2}'),
        _write_fixture(tmp_path, "apps/beta/fixtures/beta.json", '{"c": 3}'),
        "apps/missing/fixtures/missing.json",
    ]

    expected: dict[str, hashlib._Hash] = {}
    for fixture in sorted(fixtures):
        path = tmp_path / fixture
        parts = path.relative_to(tmp_path).parts
        label = parts[1] if len(parts) >= 3 and parts[0] == "apps" else "global"
        digest = expected.setdefault(label, hashlib.md5(usedforsecurity=False))
        try:
            digest.update(str(path.relative_to(tmp_path)).encode("utf-8"))
            digest.update(path.read_bytes())
        except OSError:
            continue

    assert env_refresh_module._fixture_hashes_by_app(fixtures) == {
        label: digest.hexdigest() for label, digest in expected.items()
    }
