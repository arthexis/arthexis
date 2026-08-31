from ..common_imports import *


class TransactionExportForm(forms.Form):
    start = forms.DateTimeField(required=False)
    end = forms.DateTimeField(required=False)
    chargers = forms.ModelMultipleChoiceField(
        queryset=Charger.objects.all(), required=False
    )


class TransactionImportForm(forms.Form):
    file = forms.FileField()


def export_transactions_payload(
    *,
    start: datetime | None,
    end: datetime | None,
    chargers: list[str] | None,
) -> dict[str, object]:
    return export_transactions(start=start, end=end, chargers=chargers)


def import_transactions_payload(uploaded_file) -> int:
    data = json.load(uploaded_file)
    return import_transactions_data(data)
