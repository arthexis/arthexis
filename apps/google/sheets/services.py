"""Service helpers for loading Google Sheets metadata and headers."""

from __future__ import annotations

import requests
from django.core.exceptions import ValidationError

from .models import GoogleSheet, SheetLoadResult


class GoogleSheetClient:
    """Thin API client for Google Sheets public and token-authenticated reads."""

    timeout = 12

    def load_headers(self, sheet: GoogleSheet) -> SheetLoadResult:
        """Load basic metadata and first-row headers for a tracked sheet."""

        if sheet.drive_account_id and sheet.drive_account and sheet.drive_account.access_token:
            return self._load_private(sheet)
        return self._load_public(sheet)

    def _load_private(self, sheet: GoogleSheet) -> SheetLoadResult:
        token = sheet.drive_account.access_token
        headers = {"Authorization": f"Bearer {token}"}
        meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet.spreadsheet_id}"
        meta_response = requests.get(meta_url, headers=headers, timeout=self.timeout)
        meta_response.raise_for_status()
        metadata = meta_response.json()
        sheets = metadata.get("sheets") or []
        first_sheet = sheets[0] if sheets else {}
        props = first_sheet.get("properties") or {}
        worksheet_title = props.get("title", "Sheet1")

        values_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet.spreadsheet_id}/values/"
            f"{worksheet_title}!1:1"
        )
        values_response = requests.get(values_url, headers=headers, timeout=self.timeout)
        values_response.raise_for_status()
        values_payload = values_response.json()
        rows = values_payload.get("values") or []
        row = rows[0] if rows else []
        return SheetLoadResult(
            title=metadata.get("properties", {}).get("title", ""),
            worksheet_title=worksheet_title,
            headers=[str(value).strip() for value in row if str(value).strip()],
            metadata={
                "sheet_count": len(sheets),
                "spreadsheet_id": sheet.spreadsheet_id,
            },
        )

    def _load_public(self, sheet: GoogleSheet) -> SheetLoadResult:
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet.spreadsheet_id}/gviz/tq?tqx=out:csv"
        response = requests.get(csv_url, timeout=self.timeout)
        response.raise_for_status()
        first_line = response.text.splitlines()[0] if response.text else ""
        headers = [part.strip().strip('"') for part in first_line.split(",") if part.strip()]
        if not headers:
            raise ValidationError("Could not detect headers in the public sheet.")
        return SheetLoadResult(
            title=sheet.title,
            worksheet_title=sheet.worksheet_title or "Sheet1",
            headers=headers,
            metadata={
                "sheet_count": 1,
                "source": "public_csv",
                "spreadsheet_id": sheet.spreadsheet_id,
            },
        )


def load_sheet_headers(sheet: GoogleSheet) -> SheetLoadResult:
    """Load and return headers/metadata for the provided sheet."""

    client = GoogleSheetClient()
    return client.load_headers(sheet)
