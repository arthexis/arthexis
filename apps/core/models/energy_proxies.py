from apps.energy import models as energy_models
from apps.energy import models as energy_models
from apps.maps import models as map_models


class EnergyTariff(energy_models.EnergyTariff):
    class Meta(energy_models.EnergyTariff.Meta):
        proxy = True
        app_label = "core"


class Location(map_models.Location):
    class Meta(map_models.Location.Meta):
        proxy = True
        app_label = "core"


class CustomerAccount(energy_models.CustomerAccount):
    class Meta(energy_models.CustomerAccount.Meta):
        proxy = True
        app_label = "core"


class EnergyCredit(energy_models.EnergyCredit):
    class Meta(energy_models.EnergyCredit.Meta):
        proxy = True
        app_label = "core"


class EnergyTransaction(energy_models.EnergyTransaction):
    class Meta(energy_models.EnergyTransaction.Meta):
        proxy = True
        app_label = "core"


class ClientReportSchedule(energy_models.ClientReportSchedule):
    class Meta(energy_models.ClientReportSchedule.Meta):
        proxy = True
        app_label = "core"


class ClientReport(energy_models.ClientReport):
    class Meta(energy_models.ClientReport.Meta):
        proxy = True
        app_label = "core"
