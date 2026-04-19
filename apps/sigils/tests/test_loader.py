import pytest
from django.db import OperationalError

from apps.sigils import loader
from apps.sigils.models import SigilRoot


def test_load_fixture_sigil_roots_skips_non_final_post_migrate_signal(
    monkeypatch,
) -> None:
    saved_prefixes: list[str] = []

    monkeypatch.setattr(loader, "is_final_post_migrate_app", lambda app_config: False)
    monkeypatch.setattr(
        loader,
        "_iter_fixture_entries",
        lambda _path: [
            {
                "prefix": "late",
                "context_type": SigilRoot.Context.CONFIG,
            }
        ],
    )
    monkeypatch.setattr(
        loader,
        "_save_sigil_root",
        lambda **kwargs: saved_prefixes.append(kwargs["prefix"]),
    )

    loader.load_fixture_sigil_roots(
        sender=object(),
        app_config=object(),
        using="default",
    )

    assert saved_prefixes == []


def test_load_fixture_sigil_roots_manual_call_bypasses_post_migrate_guard(
    monkeypatch,
) -> None:
    saved_prefixes: list[str] = []

    monkeypatch.setattr(loader, "is_final_post_migrate_app", lambda app_config: False)
    monkeypatch.setattr(
        loader,
        "_iter_fixture_entries",
        lambda _path: [
            {
                "prefix": "manual",
                "context_type": SigilRoot.Context.CONFIG,
            }
        ],
    )
    monkeypatch.setattr(
        loader,
        "_save_sigil_root",
        lambda **kwargs: saved_prefixes.append(kwargs["prefix"]),
    )

    loader.load_fixture_sigil_roots(using="default")

    assert saved_prefixes == ["manual"]



@pytest.mark.django_db
def test_load_fixture_sigil_roots_retries_on_locked(monkeypatch, caplog):
    caplog.set_level("WARNING")

    entries = iter([{"prefix": "retry", "context_type": "dummy"}])
    monkeypatch.setattr(loader, "_iter_fixture_entries", lambda _path: entries)

    class FlakyManager:
        attempts = 0

        def update_or_create(self, **kwargs):
            self.__class__.attempts += 1
            if self.attempts == 1:
                raise OperationalError("database is locked")
            return SigilRoot.all_objects.using("default").update_or_create(**kwargs)

    manager = FlakyManager()

    reset_calls = {"count": 0}

    monkeypatch.setattr(loader, "_get_sigil_manager", lambda _using: manager)
    monkeypatch.setattr(
        loader,
        "_reset_connection",
        lambda using: reset_calls.__setitem__("count", reset_calls["count"] + 1)
        or manager,
    )

    loader.load_fixture_sigil_roots(using="default")

    assert manager.attempts == 2
    assert reset_calls["count"] == 1
    assert SigilRoot.objects.filter(prefix="retry").exists()
    assert any("Retrying SigilRoot save" in msg for msg in caplog.messages)
