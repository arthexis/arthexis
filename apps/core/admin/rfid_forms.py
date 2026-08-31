from django import forms
from django.utils.translation import gettext_lazy as _
from import_export.forms import (
    ConfirmImportForm,
    ImportForm,
    SelectableFieldsExportForm,
)


class RFIDImportForm(ImportForm):
    account_field = forms.ChoiceField(
        choices=(
            ("id", _("Energy account IDs")),
            ("name", _("Energy account names")),
        ),
        initial="id",
        label=_("Energy accounts"),
        required=False,
    )

    field_order = ["resource", "import_file", "format", "account_field"]

    def __init__(self, formats, resources, **kwargs):
        super().__init__(formats, resources, **kwargs)
        self.fields["account_field"].initial = (
            self.data.get("account_field")
            if hasattr(self, "data") and self.data
            else "id"
        )


class RFIDExportForm(SelectableFieldsExportForm):
    account_field = forms.ChoiceField(
        choices=(
            ("id", _("Energy account IDs")),
            ("name", _("Energy account names")),
        ),
        initial="id",
        label=_("Energy accounts"),
        required=False,
    )

    field_order = ["resource", "format", "account_field"]

    def __init__(self, formats, resources, **kwargs):
        super().__init__(formats, resources, **kwargs)
        if hasattr(self, "data") and self.data:
            self.fields["account_field"].initial = self.data.get("account_field", "id")


class RFIDConfirmImportForm(ConfirmImportForm):
    account_field = forms.CharField(widget=forms.HiddenInput(), required=False)

    def clean_account_field(self):
        value = (self.cleaned_data.get("account_field") or "id").lower()
        if value not in {"id", "name"}:
            return "id"
        return value
