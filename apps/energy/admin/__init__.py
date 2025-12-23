from .customer_account_admin import CustomerAccountAdmin, EnergyTransactionAdmin
from .forms import CustomerAccountRFIDForm, OdooCustomerSearchForm
from .report_admin import ClientReportAdmin
from .tariff_admin import EnergyTariffAdmin

__all__ = [
    "ClientReportAdmin",
    "CustomerAccountAdmin",
    "CustomerAccountRFIDForm",
    "EnergyTariffAdmin",
    "EnergyTransactionAdmin",
    "OdooCustomerSearchForm",
]
