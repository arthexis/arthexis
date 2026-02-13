"""Tests for Google Sheets header loading services."""

from __future__ import annotations

from unittest.mock import Mock, patch

from apps.google.sheets.models import GoogleSheet
from apps.google.sheets.services import load_sheet_headers


def test_load_public_headers_from_csv(db):
    """Public sheet header loading should parse the first CSV row."""

    sheet = GoogleSheet.objects.create(spreadsheet_id="public-id", is_public=True)

    response = Mock()
    response.text = 'name,email,city\nAlice,alice@example.com,Paris\n'
    response.raise_for_status = Mock()

    with patch("apps.google.sheets.services.requests.get", return_value=response):
        result = load_sheet_headers(sheet)

    assert result.headers == ["name", "email", "city"]
