import json
from pathlib import Path

import pytest

from apps.core.system.lifecycle_ownership import (
    LifecycleMode,
    OwnershipError,
    OwnershipLayout,
    classify_managed_installation,
    record_managed_installation,
)


def test_source_checkout_without_metadata_is_unmanaged(tmp_path):
    checkout = tmp_path / "app"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    (checkout / ".venv").mkdir()
    (checkout / "db.sqlite3").write_text("dev", encoding="utf-8")

    installation = classify_managed_installation(tmp_path)

    assert installation.mode is LifecycleMode.UNMANAGED
    assert installation.valid is True
    assert installation.installation_id is None
    assert installation.problems == ()


def test_recorded_layout_is_managed_and_preserves_identity(tmp_path):
    checkout = tmp_path / "app"
    checkout.mkdir()

    first = record_managed_installation(tmp_path)
    second = record_managed_installation(tmp_path)

    assert first.mode is LifecycleMode.MANAGED
    assert first.valid is True
    assert first.installation_id
    assert second.installation_id == first.installation_id
    assert second.metadata_version == 1


def test_metadata_without_checkout_is_not_claimed_as_managed(tmp_path):
    checkout = tmp_path / "app"
    checkout.mkdir()
    recorded = record_managed_installation(tmp_path)
    checkout.rmdir()

    installation = classify_managed_installation(tmp_path)

    assert recorded.mode is LifecycleMode.MANAGED
    assert installation.mode is LifecycleMode.UNMANAGED
    assert installation.valid is False
    assert "managed checkout is missing" in installation.problems


def test_conflicting_metadata_is_invalid_and_not_overwritten(tmp_path):
    checkout = tmp_path / "app"
    checkout.mkdir()
    layout = OwnershipLayout.from_root(tmp_path)
    layout.metadata.parent.mkdir(parents=True)
    layout.metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": "arthexis",
                "installation_id": "existing-id",
                "root": str((tmp_path / "somewhere-else").resolve()),
                "checkout": str(checkout.resolve()),
            }
        ),
        encoding="utf-8",
    )

    installation = classify_managed_installation(tmp_path)

    assert installation.mode is LifecycleMode.UNMANAGED
    assert installation.valid is False
    assert "ownership metadata root does not match this installation" in installation.problems
    with pytest.raises(OwnershipError):
        record_managed_installation(tmp_path)
    assert json.loads(layout.metadata.read_text(encoding="utf-8"))["installation_id"] == "existing-id"


def test_invalid_metadata_is_not_managed(tmp_path):
    checkout = tmp_path / "app"
    checkout.mkdir()
    layout = OwnershipLayout.from_root(tmp_path)
    layout.metadata.parent.mkdir(parents=True)
    layout.metadata.write_text("not-json", encoding="utf-8")

    installation = classify_managed_installation(tmp_path)

    assert installation.mode is LifecycleMode.UNMANAGED
    assert installation.valid is False
    assert installation.problems[0].startswith("invalid ownership metadata:")


def test_ownership_layout_distinguishes_disposable_and_persistent_resources(tmp_path):
    layout = OwnershipLayout.from_root(tmp_path)

    assert layout.checkout == tmp_path / "app"
    assert layout.environment == tmp_path / ".venv"
    assert layout.metadata == tmp_path / ".gway" / "arthexis.json"
    assert layout.data == tmp_path / "var" / "lib"
    assert layout.data in layout.persistent_resources
    assert layout.data not in layout.managed_resources
    assert layout.checkout in layout.managed_resources
    assert layout.environment in layout.managed_resources
    assert layout.logs in layout.managed_resources
    assert layout.cache in layout.managed_resources
    assert layout.run in layout.managed_resources


def test_record_requires_expected_managed_checkout(tmp_path):
    with pytest.raises(OwnershipError, match="managed checkout is missing"):
        record_managed_installation(tmp_path)
