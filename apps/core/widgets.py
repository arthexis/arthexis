from collections import OrderedDict
from typing import Any

from django import forms
from django.forms.widgets import ClearableFileInput
import json


class CopyColorWidget(forms.TextInput):
    input_type = "color"
    template_name = "widgets/copy_color.html"

    class Media:
        js = ["core/copy_color.js"]


class CodeEditorWidget(forms.Textarea):
    """Simple code editor widget for editing recipes."""

    def __init__(self, attrs=None):
        default_attrs = {"class": "code-editor"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    class Media:
        css = {"all": ["core/code_editor.css"]}
        js = ["core/code_editor.js"]


class OdooProductWidget(forms.Select):
    """Widget for selecting an Odoo product."""

    template_name = "widgets/odoo_product.html"

    class Media:
        js = ["core/odoo_product.js"]

    def get_context(self, name, value, attrs):
        attrs = attrs or {}
        if isinstance(value, dict):
            attrs["data-current-id"] = str(value.get("id", ""))
            value = json.dumps(value)
        elif not value:
            value = ""
        return super().get_context(name, value, attrs)

    def value_from_datadict(self, data, files, name):
        raw = data.get(name)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}


class AdminBase64FileWidget(ClearableFileInput):
    """Clearable file input that exposes base64 data for downloads."""

    template_name = "widgets/admin_base64_file.html"

    def __init__(
        self,
        *,
        download_name: str | None = None,
        content_type: str = "application/octet-stream",
        **kwargs,
    ) -> None:
        self.download_name = download_name
        self.content_type = content_type
        super().__init__(**kwargs)

    def is_initial(self, value):
        if isinstance(value, str):
            return bool(value)
        return super().is_initial(value)

    def format_value(self, value):
        if isinstance(value, str):
            return value
        return super().format_value(value)

    def get_context(self, name, value, attrs):
        if isinstance(value, str):
            base64_value = value.strip()
            rendered_value = None
        else:
            base64_value = None
            rendered_value = value
        context = super().get_context(name, rendered_value, attrs)
        widget_context = context["widget"]
        widget_context["is_initial"] = bool(base64_value)
        widget_context["base64_value"] = base64_value
        widget_context["download_name"] = self.download_name or f"{name}.bin"
        widget_context["content_type"] = self.content_type
        return context


class RFIDDataWidget(forms.Textarea):
    """Render RFID block dumps as a readable grid while keeping raw JSON editable."""

    template_name = "admin/core/widgets/rfid_data_widget.html"

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        default_attrs = {
            "class": "vLargeTextField rfid-data-widget__input",
            "rows": 8,
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    class Media:
        css = {"all": ["core/rfid_data_widget.css"]}
        js = ["core/rfid_data_widget.js"]

    def format_value(self, value):  # noqa: D401 - inherits docs
        if value in ({}, []):
            return "[]"
        if value is None:
            return "[]"
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            try:
                return json.dumps(list(value), indent=2, sort_keys=True)
            except Exception:
                return "[]"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        parsed_entries = self._parse_entries(value)
        context["widget"]["sectors"] = self._build_sectors(parsed_entries)
        context["widget"]["has_parse_error"] = bool(
            context["widget"].get("value") and not parsed_entries
        )
        context["widget"]["byte_headers"] = [f"{index:02X}" for index in range(16)]
        return context

    def _parse_entries(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return []
        if not isinstance(value, list):
            return []

        entries: list[dict[str, Any]] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            block = entry.get("block")
            data = entry.get("data")
            if not isinstance(block, int) or not isinstance(data, (list, tuple)):
                continue

            bytes_: list[str] = []
            raw_bytes: list[int] = []
            valid = True
            for raw in list(data)[:16]:
                try:
                    byte_value = int(raw)
                except (TypeError, ValueError):
                    valid = False
                    break
                byte_value = max(0, min(255, byte_value))
                raw_bytes.append(byte_value)
                bytes_.append(f"{byte_value:02X}")
            if not valid:
                continue

            if len(bytes_) < 16:
                bytes_.extend(["--"] * (16 - len(bytes_)))
                raw_bytes.extend([0] * (16 - len(raw_bytes)))

            text_chars: list[str] = []
            for byte_value in raw_bytes:
                if 32 <= byte_value <= 126:
                    text_chars.append(chr(byte_value))
                else:
                    text_chars.append("·")
            text_value = "".join(text_chars)

            entries.append(
                {
                    "block": block,
                    "sector": block // 4,
                    "offset": block % 4,
                    "key": entry.get("key"),
                    "bytes": bytes_,
                    "is_trailer": block % 4 == 3,
                    "text_value": text_value,
                }
            )

        return sorted(entries, key=lambda item: item["block"])

    def _build_sectors(self, entries: list[dict[str, Any]]):
        sectors: OrderedDict[int, dict[str, Any]] = OrderedDict()
        for entry in entries:
            sector_key = entry["sector"]
            sector = sectors.setdefault(
                sector_key,
                {"sector": sector_key, "blocks": []},
            )
            sector["blocks"].append(entry)
        for sector in sectors.values():
            sector["blocks"].sort(key=lambda block: block["offset"])
        return list(sectors.values())
