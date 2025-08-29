from django import forms


class CopyColorWidget(forms.TextInput):
    input_type = "color"
    template_name = "widgets/copy_color.html"

    class Media:
        js = ["app/copy_color.js"]


class CodeEditorWidget(forms.Textarea):
    """Simple code editor widget for editing recipes."""

    def __init__(self, attrs=None):
        default_attrs = {"class": "code-editor"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    class Media:
        css = {"all": ["app/code_editor.css"]}
        js = ["app/code_editor.js"]
