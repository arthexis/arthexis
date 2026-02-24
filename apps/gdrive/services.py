from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from django.db import transaction
from django.utils.dateparse import parse_datetime

from .models import GoogleAccount, GoogleSheet, GoogleSheetColumn


class GoogleSheetsError(RuntimeError):
    """Base exception raised for Google Sheets integration failures."""


class GoogleSheetsRequestError(GoogleSheetsError):
    """Raised when Google Sheets API returns an error response."""


@dataclass(frozen=True)
class VirtualTable:
    """Tabular representation of a Google worksheet for downstream operations."""

    columns: list[str]
    rows: list[dict[str, Any]]


class GoogleSheetsGateway:
    """Gateway used by future code to introspect and operate on tracked spreadsheets."""

    base_url = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self, account: GoogleAccount):
        self.account = account

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        token = self.account.get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")
        response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise GoogleSheetsRequestError(
                f"Google Sheets API request failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise GoogleSheetsRequestError("Google Sheets API returned a non-object payload.")
        return payload

    def fetch_sheet_metadata(self, sheet: GoogleSheet) -> dict[str, Any]:
        """Fetch and persist spreadsheet metadata."""
        url = f"{self.base_url}/{sheet.spreadsheet_id}"
        payload = self._request("GET", url, params={"includeGridData": "false"})
        sheet.metadata = payload
        sheet.save(update_fields=["metadata", "updated"])
        return payload

    def read_virtual_table(
        self,
        sheet: GoogleSheet,
        worksheet: str | None = None,
        row_limit: int | None = None,
    ) -> VirtualTable:
        """Read worksheet rows and return them as dictionaries keyed by introspected headers."""
        target = worksheet or sheet.default_worksheet
        rng = f"{target}!A1:ZZ"
        url = f"{self.base_url}/{sheet.spreadsheet_id}/values/{rng}"
        payload = self._request("GET", url)
        values = payload.get("values", [])
        if not values:
            return VirtualTable(columns=[], rows=[])
        headers = [str(value).strip() for value in values[0]]
        data_rows = values[1:]
        if row_limit is not None:
            data_rows = data_rows[:row_limit]
        rows: list[dict[str, Any]] = []
        for row in data_rows:
            mapped = {header: row[idx] if idx < len(row) else "" for idx, header in enumerate(headers)}
            rows.append(mapped)
        return VirtualTable(columns=headers, rows=rows)

    def append_rows(
        self,
        sheet: GoogleSheet,
        rows: list[dict[str, Any]],
        worksheet: str | None = None,
    ) -> dict[str, Any]:
        """Append rows to a worksheet using tracked column ordering."""
        if not rows:
            return {"updatedRows": 0}

        target = worksheet or sheet.default_worksheet
        columns = self.get_or_introspect_columns(sheet, target)
        ordered_headers = [column.name for column in columns]
        body_rows = [[row.get(header, "") for header in ordered_headers] for row in rows]

        rng = f"{target}!A1"
        url = f"{self.base_url}/{sheet.spreadsheet_id}/values/{rng}:append"
        return self._request(
            "POST",
            url,
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": body_rows},
        )

    @transaction.atomic
    def introspect_columns(
        self,
        sheet: GoogleSheet,
        worksheet: str | None = None,
        sample_size: int = 50,
    ) -> list[GoogleSheetColumn]:
        """Inspect worksheet headers and sample rows to track virtual-table schema."""
        table = self.read_virtual_table(sheet, worksheet=worksheet, row_limit=sample_size)
        target = worksheet or sheet.default_worksheet

        GoogleSheetColumn.objects.filter(sheet=sheet, worksheet=target).delete()

        columns: list[GoogleSheetColumn] = []
        for index, header in enumerate(table.columns):
            values = [row.get(header, "") for row in table.rows]
            detected = self._infer_column_type(values)
            column = GoogleSheetColumn.objects.create(
                sheet=sheet,
                worksheet=target,
                name=header or f"column_{index + 1}",
                position=index,
                detected_type=detected,
            )
            columns.append(column)
        return columns

    def get_or_introspect_columns(
        self,
        sheet: GoogleSheet,
        worksheet: str | None = None,
    ) -> list[GoogleSheetColumn]:
        """Return tracked columns, introspecting when cache is empty."""
        target = worksheet or sheet.default_worksheet
        columns = list(
            GoogleSheetColumn.objects.filter(sheet=sheet, worksheet=target).order_by("position")
        )
        if columns:
            return columns
        return self.introspect_columns(sheet=sheet, worksheet=target)

    def _infer_column_type(self, values: list[Any]) -> str:
        """Infer a primitive type from non-empty sample values."""
        normalized = [str(value).strip() for value in values if str(value).strip()]
        if not normalized:
            return GoogleSheetColumn.ColumnType.STRING

        if all(value.lower() in {"true", "false"} for value in normalized):
            return GoogleSheetColumn.ColumnType.BOOLEAN

        if all(self._is_int(value) for value in normalized):
            return GoogleSheetColumn.ColumnType.INTEGER

        if all(self._is_float(value) for value in normalized):
            return GoogleSheetColumn.ColumnType.FLOAT

        if all(self._is_datetime(value) for value in normalized):
            return GoogleSheetColumn.ColumnType.DATETIME

        return GoogleSheetColumn.ColumnType.STRING

    @staticmethod
    def _is_int(value: str) -> bool:
        try:
            int(value)
        except ValueError:
            return False
        return True

    @staticmethod
    def _is_float(value: str) -> bool:
        try:
            float(value)
        except ValueError:
            return False
        return True

    @staticmethod
    def _is_datetime(value: str) -> bool:
        parsed = parse_datetime(value)
        if parsed is not None:
            return True
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True
