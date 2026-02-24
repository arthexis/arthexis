from __future__ import annotations

from types import MethodType

import pytest
from django.contrib.auth import get_user_model

from apps.gdrive.models import GoogleAccount, GoogleSheet, GoogleSheetColumn
from apps.gdrive.services import GoogleSheetsGateway


@pytest.mark.django_db
def test_introspect_columns_detects_basic_types():
    """Column introspection should infer types from sampled worksheet values."""

    user = get_user_model().objects.create_user(username="gdrive-user", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="gdrive@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    sheet = GoogleSheet.objects.create(
        name="Ops sheet",
        account=account,
        spreadsheet_id="spreadsheet-1",
        default_worksheet="Sheet1",
    )
    gateway = GoogleSheetsGateway(account)

    def fake_read(self, tracked_sheet, worksheet=None, row_limit=None):
        from apps.gdrive.services import VirtualTable

        return VirtualTable(
            columns=["name", "enabled", "count", "ratio", "created_at"],
            rows=[
                {
                    "name": "alpha",
                    "enabled": "true",
                    "count": "10",
                    "ratio": "10.5",
                    "created_at": "2026-01-01T10:11:12",
                },
                {
                    "name": "beta",
                    "enabled": "false",
                    "count": "12",
                    "ratio": "11.1",
                    "created_at": "2026-01-02T10:11:12",
                },
            ],
        )

    gateway.read_virtual_table = MethodType(fake_read, gateway)
    columns = gateway.introspect_columns(sheet)

    assert [column.name for column in columns] == ["name", "enabled", "count", "ratio", "created_at"]
    assert [column.detected_type for column in columns] == [
        GoogleSheetColumn.ColumnType.STRING,
        GoogleSheetColumn.ColumnType.BOOLEAN,
        GoogleSheetColumn.ColumnType.INTEGER,
        GoogleSheetColumn.ColumnType.FLOAT,
        GoogleSheetColumn.ColumnType.DATETIME,
    ]


@pytest.mark.django_db
def test_get_or_introspect_columns_reuses_cached_columns():
    """Tracked columns should be reused without requesting another introspection."""

    user = get_user_model().objects.create_user(username="gdrive-cache-user", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="cache@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    sheet = GoogleSheet.objects.create(
        name="Cache sheet",
        account=account,
        spreadsheet_id="spreadsheet-cache",
        default_worksheet="Sheet1",
    )
    gateway = GoogleSheetsGateway(account)
    cached = GoogleSheetColumn.objects.create(
        sheet=sheet,
        worksheet=sheet.default_worksheet,
        name="cached",
        position=0,
        detected_type=GoogleSheetColumn.ColumnType.STRING,
    )

    columns = gateway.get_or_introspect_columns(sheet)

    assert columns == [cached]

@pytest.mark.django_db
def test_fetch_sheet_metadata_persists_metadata_only(monkeypatch):
    """Metadata fetch should persist cache without referencing unknown model fields."""

    user = get_user_model().objects.create_user(username="gdrive-meta-user", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="meta@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    sheet = GoogleSheet.objects.create(
        name="Meta sheet",
        account=account,
        spreadsheet_id="spreadsheet-meta",
        default_worksheet="Sheet1",
    )
    gateway = GoogleSheetsGateway(account)

    def fake_request(self, method, url, **kwargs):
        return {"spreadsheetId": sheet.spreadsheet_id, "properties": {"title": "Meta"}}

    gateway._request = MethodType(fake_request, gateway)

    saved_update_fields = {}
    original_save = GoogleSheet.save

    def tracking_save(self, *args, **kwargs):
        saved_update_fields["value"] = kwargs.get("update_fields")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(GoogleSheet, "save", tracking_save)

    payload = gateway.fetch_sheet_metadata(sheet)

    assert payload["spreadsheetId"] == "spreadsheet-meta"
    assert saved_update_fields["value"] == ["metadata"]


@pytest.mark.django_db
def test_append_rows_uses_input_rows_when_schema_empty():
    """Append should preserve provided values when introspection yields no headers."""

    user = get_user_model().objects.create_user(username="gdrive-append-user", password="x")
    account = GoogleAccount.objects.create(
        user=user,
        email="append@example.com",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    sheet = GoogleSheet.objects.create(
        name="Append sheet",
        account=account,
        spreadsheet_id="spreadsheet-append",
        default_worksheet="Sheet1",
    )
    gateway = GoogleSheetsGateway(account)

    gateway.get_or_introspect_columns = MethodType(lambda self, tracked_sheet, worksheet=None: [], gateway)

    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return {"updatedRows": 1}

    gateway._request = MethodType(fake_request, gateway)

    result = gateway.append_rows(sheet, rows=[{"name": "alpha", "count": "2"}])

    assert result["updatedRows"] == 1
    assert captured["json"] == {"values": [["alpha", "2"]]}
