"""Regression tests for ``Reference`` natural-key resolution."""

import pytest

from apps.links.models import Reference


@pytest.mark.django_db
def test_reference_manager_get_by_natural_key_uses_value_when_provided():
    """Regression: fixture natural keys remain deterministic with duplicate titles."""

    first = Reference(alt_text="SQLite", value="https://www.sqlite.org/faq.html")
    expected = Reference(alt_text="SQLite", value="https://www.sqlite.org/")
    Reference.objects.bulk_create([first, expected])

    resolved = Reference.objects.get_by_natural_key("SQLite", "https://www.sqlite.org/")

    assert resolved == expected


@pytest.mark.django_db
def test_reference_manager_get_by_natural_key_keeps_legacy_lookup():
    """Legacy single-part natural keys still resolve when title is unique."""

    expected = Reference(alt_text="Python", value="https://www.python.org/")
    Reference.objects.bulk_create([expected])

    resolved = Reference.objects.get_by_natural_key("Python")

    assert resolved == expected
