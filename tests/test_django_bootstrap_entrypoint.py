from __future__ import annotations


def test_settings_import_runs_shared_bootstrap(monkeypatch) -> None:
    """The settings package owns process bootstrap for direct Django setup."""
    import config.bootstrap as bootstrap

    calls: list[str] = []
    monkeypatch.setattr(bootstrap, "_bootstrapped", False)
    monkeypatch.setattr(bootstrap, "loadenv", lambda: calls.append("loadenv"))
    monkeypatch.setattr(
        bootstrap,
        "bootstrap_sqlite_driver",
        lambda: calls.append("sqlite"),
    )

    bootstrap.bootstrap_django_environment()
    bootstrap.bootstrap_django_environment()

    assert calls == ["loadenv", "sqlite"]
