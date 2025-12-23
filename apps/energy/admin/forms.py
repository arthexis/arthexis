from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from ..models import CustomerAccount


class CustomerAccountRFIDForm(forms.ModelForm):
    """Form for assigning existing RFIDs to a customer account."""

    class Meta:
        model = CustomerAccount.rfids.through
        fields = ["rfid"]

    def clean_rfid(self):
        rfid = self.cleaned_data["rfid"]
        if rfid.energy_accounts.exclude(pk=self.instance.customeraccount_id).exists():
            raise forms.ValidationError(
                "RFID is already assigned to another customer account"
            )
        return rfid


class OdooCustomerSearchForm(forms.Form):
    name = forms.CharField(required=False, label=_("Name contains"))
    email = forms.CharField(required=False, label=_("Email contains"))
    phone = forms.CharField(required=False, label=_("Phone contains"))
    limit = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=200,
        initial=50,
        label=_("Result limit"),
        help_text=_("Limit the number of Odoo customers returned per search."),
    )
