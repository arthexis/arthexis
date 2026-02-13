"""Tests for Google Sheet model utilities."""

from apps.google.sheets.models import GoogleSheet


def test_spreadsheet_id_from_url_extracts_id():
    """Spreadsheet ids should be extracted from standard Google Sheet URLs."""

    sheet_id = GoogleSheet.spreadsheet_id_from_url(
        "https://docs.google.com/spreadsheets/d/abc123DEF_-/edit#gid=0"
    )
    assert sheet_id == "abc123DEF_-"
