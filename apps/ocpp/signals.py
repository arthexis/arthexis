from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.counters.models import DashboardRule

from .models import Charger, PublicConnectorPage, Simulator


@receiver([post_save, post_delete], sender=Simulator)
def invalidate_simulator_dashboard_rule_cache(sender, **_kwargs) -> None:
    """Invalidate dashboard rule cache for CP simulator default checks."""

    DashboardRule.invalidate_model_cache(sender)


@receiver(post_save, sender=Charger)
def ensure_public_connector_page(sender, instance: Charger, **_kwargs) -> None:
    """Ensure every charger has a public connector page and QR assets."""

    page, _created = PublicConnectorPage.objects.get_or_create(charger=instance)
    if page.qr_svg and page.qr_png:
        return
    try:
        page.refresh_qr_assets(page.public_url())
        page.save(update_fields=["qr_png", "qr_svg", "updated_at"])
    except RuntimeError:
        return
