from django.apps import apps as django_apps
from django.conf import settings

from .customer_account_admin import CustomerAccountAdmin, EnergyTransactionAdmin
from .forms import CustomerAccountRFIDForm, OdooCustomerSearchForm
from .tariff_admin import EnergyTariffAdmin

__all__ = [
    "CustomerAccountAdmin",
    "CustomerAccountRFIDForm",
    "EnergyTariffAdmin",
    "EnergyTransactionAdmin",
    "OdooCustomerSearchForm",
]

if getattr(settings, "CELERY_RUNTIME_ENABLED", True) and django_apps.is_installed(
    "apps.ocpp"
):
    from .report_admin import ClientReportAdmin

    __all__.append("ClientReportAdmin")
