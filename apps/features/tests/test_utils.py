from django.core.cache import cache
from django.db import connection

from apps.features.utils import (
    _feature_cache_key_for_current_connection,
    clear_feature_cache,
)


def test_feature_cache_key_preserves_default_for_non_test_database(monkeypatch) -> None:
    monkeypatch.setitem(connection.settings_dict, "NAME", "arthexis")

    assert _feature_cache_key_for_current_connection("feature-enabled:sample") == (
        "feature-enabled:sample"
    )


def test_feature_cache_key_is_namespaced_for_test_database(monkeypatch) -> None:
    monkeypatch.setitem(connection.settings_dict, "NAME", "test_arthexis_gw0")

    assert _feature_cache_key_for_current_connection("feature-enabled:sample") == (
        "feature-enabled:sample:default:test_arthexis_gw0"
    )


def test_feature_cache_key_handles_pathlike_test_database_name(monkeypatch) -> None:
    monkeypatch.setitem(connection.settings_dict, "NAME", "/tmp/arthexis/test_db.sqlite3")
    monkeypatch.setitem(
        connection.settings_dict,
        "TEST",
        {"NAME": "/tmp/arthexis/test_db.sqlite3"},
    )

    assert _feature_cache_key_for_current_connection("feature-enabled:sample") == (
        "feature-enabled:sample:default:/tmp/arthexis/test_db.sqlite3"
    )


def test_clear_feature_cache_deletes_namespaced_test_database_key(
    monkeypatch,
) -> None:
    monkeypatch.setitem(connection.settings_dict, "NAME", "test_arthexis_gw0")
    cache_key = "feature-enabled:sample-clear"
    namespaced_cache_key = _feature_cache_key_for_current_connection(cache_key)
    cache.set(cache_key, False)
    cache.set(namespaced_cache_key, True)

    try:
        clear_feature_cache(cache_key)

        assert cache.get(namespaced_cache_key) is None
        assert cache.get(cache_key) is False
    finally:
        cache.delete(cache_key)
        cache.delete(namespaced_cache_key)
