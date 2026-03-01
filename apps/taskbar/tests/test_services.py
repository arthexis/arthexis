"""Tests for taskbar icon selection services."""

from __future__ import annotations

import pytest

from apps.taskbar.models import TaskbarIcon
from apps.taskbar.services import TaskbarIconNotFoundError, TaskbarIconSelector


@pytest.mark.django_db
def test_get_active_icon_prefers_lock_file_slug(tmp_path):
    """Selector should resolve lock file slug before default icon fallback."""

    default_icon = TaskbarIcon.objects.create(
        name="Default",
        slug="default",
        icon_b64="ZGVmYXVsdA==",
        is_default=True,
    )
    lock_icon = TaskbarIcon.objects.create(
        name="Alt",
        slug="alt",
        icon_b64="YWx0",
        is_default=False,
    )
    selector = TaskbarIconSelector(lock_dir=tmp_path)
    selector.set_active_icon(lock_icon.slug)

    selection = selector.get_active_icon()

    assert selection.icon.pk == lock_icon.pk
    assert selection.source == "lock"
    assert default_icon.pk != lock_icon.pk


@pytest.mark.django_db
def test_get_active_icon_falls_back_to_default_when_lock_missing(tmp_path):
    """Selector should return default icon when no lock file is present."""

    default_icon = TaskbarIcon.objects.create(
        name="Default",
        slug="default",
        icon_b64="ZGVmYXVsdA==",
        is_default=True,
    )
    selector = TaskbarIconSelector(lock_dir=tmp_path)

    selection = selector.get_active_icon()

    assert selection.icon.pk == default_icon.pk
    assert selection.source == "default"


@pytest.mark.django_db
def test_set_active_icon_raises_specific_error_for_unknown_slug(tmp_path):
    """Selector should raise TaskbarIconNotFoundError for unknown icon slug."""

    selector = TaskbarIconSelector(lock_dir=tmp_path)

    with pytest.raises(TaskbarIconNotFoundError):
        selector.set_active_icon("missing")
