"""Tests for Google Sheet model utilities."""

from apps.google.sheets.models import GoogleSheet


def test_spreadsheet_id_from_url_extracts_id():
    """Spreadsheet ids should be extracted from standard Google Sheet URLs."""

    sheet_id = GoogleSheet.spreadsheet_id_from_url(
        "https://docs.google.com/spreadsheets/d/abc123DEF_-/edit#gid=0"
    )
    assert sheet_id == "abc123DEF_-"


def test_spreadsheet_id_from_url_supports_user_prefix():
    """Spreadsheet ID extraction should support /u/<n>/ URLs."""

    url = "https://docs.google.com/spreadsheets/u/0/d/1abcDEF_123/edit#gid=0"
    assert GoogleSheet.spreadsheet_id_from_url(url) == "1abcDEF_123"
