"""Regression tests for footer reference visibility.

These tests create fresh `Reference` records instead of loading fixtures so we can
isolate problems in the visibility logic itself. When a brand-new record with a
redirect validation status vanished from the footer, it demonstrated that the
hidden links came from the validation filter rather than corrupt or incomplete
fixtures.
"""

import pytest

from apps.links.models import Reference
from apps.links.reference_utils import filter_visible_references


@pytest.mark.django_db
def test_is_link_valid_allows_redirect_status_codes():
    reference = Reference.objects.create(
        alt_text="Redirecting Link",
        value="https://example.com",
        include_in_footer=True,
        validation_status=302,
    )

    assert reference.is_link_valid() is True


@pytest.mark.django_db
def test_filter_visible_references_keeps_redirects():
    reference = Reference.objects.create(
        alt_text="Moved Permanently",
        value="https://example.com",
        include_in_footer=True,
        validation_status=301,
    )

    visible = filter_visible_references([reference])

    assert visible == [reference]


@pytest.mark.django_db
def test_filter_visible_references_keeps_fresh_valid_entries():
    reference = Reference.objects.create(
        alt_text="Fresh Link",
        value="https://example.com",
        include_in_footer=True,
    )

    visible = filter_visible_references([reference])

    assert visible == [reference]


@pytest.mark.django_db
def test_filter_visible_references_excludes_invalid_status():
    reference = Reference.objects.create(
        alt_text="Server Error",
        value="https://example.com",
        include_in_footer=True,
        validation_status=500,
    )

    visible = filter_visible_references([reference])

    assert visible == []
