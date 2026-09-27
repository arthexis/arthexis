"""Instance-level OCPP admission policy."""

from django.db import models


class OcppPolicy(models.Model):
    """Persistent policy for accepting chargers into this Arthexis instance."""

    class AdmissionMode(models.TextChoices):
        OPEN = "open", "Open"
        RESTRICTED = "restricted", "Restricted"

    SINGLETON_PK = 1

    charger_admission_mode = models.CharField(
        choices=AdmissionMode.choices,
        default=AdmissionMode.OPEN,
        max_length=16,
    )
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls) -> "OcppPolicy":
        policy, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return policy

    def __str__(self) -> str:
        return f"charger admission: {self.charger_admission_mode}"
