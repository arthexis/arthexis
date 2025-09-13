from django import forms
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
