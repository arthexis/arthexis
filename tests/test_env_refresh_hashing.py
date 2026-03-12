import hashlib
import importlib.util
from pathlib import Path
from datetime import timedelta

import pytest

from django.conf import settings
from django.utils import timezone


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


@pytest.mark.django_db
def test_upsert_site_configuration_updates_existing_row(env_refresh_module):
    from apps.nginx.models import SiteConfiguration

    applied_at = timezone.now() - timedelta(hours=2)
    validated_at = timezone.now() - timedelta(hours=1)
    SiteConfiguration.objects.create(
        name="preview-example",
        enabled=False,
        last_applied_at=applied_at,
        last_validated_at=validated_at,
        last_message="runtime state",
    )

    updated = env_refresh_module._upsert_site_configuration(
        {
            "name": "preview-example",
            "enabled": True,
            "mode": "public",
            "protocol": "http",
            "role": "Terminal",
            "port": 8888,
            "certificate": None,
            "external_websockets": True,
            "managed_subdomains": "admin,api,status",
            "include_ipv6": False,
            "expected_path": "/etc/nginx/sites-enabled/arthexis-preview-example.conf",
            "site_entries_path": "apps/nginx/fixtures/data/nginx-sites-preview.json",
            "site_destination": "/etc/nginx/sites-enabled/arthexis-sites.conf",
            "last_applied_at": None,
            "last_validated_at": None,
            "last_message": "",
        }
    )

    assert updated is True
    assert SiteConfiguration.objects.filter(name="preview-example").count() == 1
    config = SiteConfiguration.objects.get(name="preview-example")
    assert config.enabled is True
    assert config.last_applied_at == applied_at
    assert config.last_validated_at == validated_at
    assert config.last_message == "runtime state"


@pytest.mark.django_db
def test_upsert_site_configuration_returns_false_when_name_missing(env_refresh_module):
    result = env_refresh_module._upsert_site_configuration({"enabled": True})
    assert result is False


def test_load_fixture_with_retry_retries_until_success(monkeypatch, env_refresh_module, capsys):
    """Regression: fixture loading should retry transient sqlite lock failures."""

    calls: list[str] = []

    def _flaky_load(command: str, fixture: str, *, verbosity: int) -> None:
        calls.append(fixture)
        if len(calls) < 3:
            raise env_refresh_module.OperationalError("database is locked")

    monkeypatch.setattr(env_refresh_module, "call_command", _flaky_load)

    env_refresh_module._load_fixture_with_retry(
        "seed.json",
        using_sqlite=True,
        attempts=3,
        base_delay=0,
    )

    output = capsys.readouterr().out
    assert "Database locked while loading seed.json" in output
    assert calls == ["seed.json", "seed.json", "seed.json"]


def test_load_fixture_with_retry_raises_after_max_attempts(monkeypatch, env_refresh_module):
    """Regression: repeated sqlite lock failures should still bubble up."""

    def _always_locked(command: str, fixture: str, *, verbosity: int) -> None:
        raise env_refresh_module.OperationalError("database is locked")

    monkeypatch.setattr(env_refresh_module, "call_command", _always_locked)

    with pytest.raises(env_refresh_module.OperationalError, match="database is locked"):
        env_refresh_module._load_fixture_with_retry(
            "seed.json",
            using_sqlite=True,
            attempts=2,
            base_delay=0,
        )


def test_load_fixtures_with_deferred_retry_retries_once(monkeypatch, env_refresh_module, capsys):
    """Regression: deferred fixtures should be retried after initial dependency loads."""

    calls: list[str] = []

    real_error = env_refresh_module.DeserializationError

    def _fake_loader(fixture: str, *, using_sqlite: bool) -> None:
        calls.append(fixture)
        if fixture == "b.json" and calls.count("b.json") == 1:
            raise real_error("dependency missing")

    monkeypatch.setattr(env_refresh_module, "_load_fixture_with_retry", _fake_loader)

    env_refresh_module._load_fixtures_with_deferred_retry(
        {1: ["a.json"], 2: ["b.json"]},
        using_sqlite=True,
    )

    assert calls == ["a.json", "b.json", "b.json"]
    assert capsys.readouterr().out == ".."


def test_load_fixtures_with_deferred_retry_reports_second_failure(
    monkeypatch, env_refresh_module, capsys
):
    """Regression: fixtures that fail twice should still be reported as skipped."""

    calls: list[str] = []
    real_error = env_refresh_module.DeserializationError

    def _always_fail(fixture: str, *, using_sqlite: bool) -> None:
        calls.append(fixture)
        if fixture == "bad.json":
            raise real_error("still missing")

    monkeypatch.setattr(env_refresh_module, "_load_fixture_with_retry", _always_fail)

    env_refresh_module._load_fixtures_with_deferred_retry(
        {1: ["good.json"], 2: ["bad.json"]},
        using_sqlite=False,
    )

    output = capsys.readouterr().out
    assert output.startswith(".")
    assert "Skipping fixture bad.json due to: still missing" in output
    assert calls == ["good.json", "bad.json", "bad.json"]


def test_load_fixtures_with_deferred_retry_handles_chained_dependencies(
    monkeypatch, env_refresh_module, capsys
):
    """Regression: deferred fixtures should keep retrying while progress is made."""

    calls: list[str] = []
    real_error = env_refresh_module.DeserializationError

    def _chained_loader(fixture: str, *, using_sqlite: bool) -> None:
        calls.append(fixture)
        if fixture == "b.json" and calls.count("b.json") == 1:
            raise real_error("b depends on a")
        if fixture == "c.json" and calls.count("c.json") < 3:
            raise real_error("c depends on b")

    monkeypatch.setattr(env_refresh_module, "_load_fixture_with_retry", _chained_loader)

    env_refresh_module._load_fixtures_with_deferred_retry(
        {1: ["a.json"], 2: ["b.json"], 3: ["c.json"]},
        using_sqlite=True,
    )

    assert calls == ["a.json", "b.json", "c.json", "b.json", "c.json", "c.json"]
    assert capsys.readouterr().out == "..."




@pytest.mark.pr_origin(6190)
def test_close_old_connections_safely_ignores_pytest_db_guard(monkeypatch, env_refresh_module):
    """Regression: pytest-django DB guard RuntimeError should be swallowed."""

    def _guarded_close() -> None:
        raise RuntimeError(
            'Database access not allowed, use the "django_db" mark, or the "db" or "transactional_db" fixtures to enable it.'
        )

    monkeypatch.setattr(env_refresh_module, "close_old_connections", _guarded_close)

    env_refresh_module._close_old_connections_safely()


@pytest.mark.pr_origin(6190)
def test_close_old_connections_safely_reraises_unexpected_runtime_error(monkeypatch, env_refresh_module):
    """Regression: unexpected RuntimeError values should still be surfaced."""

    def _broken_close() -> None:
        raise RuntimeError("unexpected close failure")

    monkeypatch.setattr(env_refresh_module, "close_old_connections", _broken_close)

    with pytest.raises(RuntimeError, match="unexpected close failure"):
        env_refresh_module._close_old_connections_safely()


def test_call_command_with_sqlite_lock_retry_retries_and_succeeds(
    monkeypatch, env_refresh_module, capsys
):
    """Regression: SQLite lock conflicts during command calls should be retried."""

    calls: list[str] = []

    def _flaky_call(command: str, *args, **kwargs) -> None:
        calls.append(command)
        if len(calls) == 1:
            raise env_refresh_module.OperationalError("database is locked")

    monkeypatch.setattr(env_refresh_module, "call_command", _flaky_call)

    env_refresh_module._call_command_with_sqlite_lock_retry(
        "register_site_apps",
        using_sqlite=True,
        attempts=3,
        base_delay=0,
    )

    output = capsys.readouterr().out
    assert "Database locked while running register_site_apps" in output
    assert calls == ["register_site_apps", "register_site_apps"]


def test_call_command_with_sqlite_lock_retry_raises_after_max_attempts(
    monkeypatch, env_refresh_module
):
    """Regression: repeated SQLite lock conflicts should still fail after retries."""

    def _always_locked(command: str, *args, **kwargs) -> None:
        raise env_refresh_module.OperationalError("database is locked")

    monkeypatch.setattr(env_refresh_module, "call_command", _always_locked)

    with pytest.raises(env_refresh_module.OperationalError, match="database is locked"):
        env_refresh_module._call_command_with_sqlite_lock_retry(
            "register_site_apps",
            using_sqlite=True,
            attempts=2,
            base_delay=0,
        )
