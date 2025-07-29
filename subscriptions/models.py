from datetime import timedelta
from django.db import models
from accounts.models import Account


class Product(models.Model):
    """A product that users can subscribe to."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    renewal_period = models.PositiveIntegerField(help_text="Renewal period in days")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class Subscription(models.Model):
    """An account's subscription to a product."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    start_date = models.DateField(auto_now_add=True)
    next_renewal = models.DateField(blank=True)

    def save(self, *args, **kwargs):
        if not self.next_renewal:
            self.next_renewal = self.start_date + timedelta(days=self.product.renewal_period)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.account.user} -> {self.product}"
