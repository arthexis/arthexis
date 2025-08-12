from django import forms


class CopyColorWidget(forms.TextInput):
    input_type = "color"
    template_name = "widgets/copy_color.html"

    class Media:
        js = ["app/copy_color.js"]
