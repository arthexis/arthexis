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
