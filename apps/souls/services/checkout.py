from __future__ import annotations

from django.http import HttpRequest

from apps.souls.models import ShopOrderSoulAttachment, Soul

CHECKOUT_SOUL_KEY = "shop_checkout_soul_id"


def attach_soul_to_order_items(*, request: HttpRequest, order_items: list) -> None:
    soul_id = request.session.get(CHECKOUT_SOUL_KEY)
    if not soul_id:
        return

    soul = Soul.objects.filter(pk=soul_id).first()
    if not soul:
        request.session.pop(CHECKOUT_SOUL_KEY, None)
        return

    for item in order_items:
        attachment, created = ShopOrderSoulAttachment.objects.get_or_create(
            order_item=item,
            defaults={"soul": soul, "preload_quantity": max(1, int(item.quantity or 1))},
        )
        if created:
            continue

        next_quantity = max(1, int(item.quantity or 1))
        update_fields: list[str] = []
        if attachment.soul_id != soul.id:
            attachment.soul = soul
            update_fields.append("soul")
        if attachment.preload_quantity != next_quantity:
            attachment.preload_quantity = next_quantity
            update_fields.append("preload_quantity")
        if update_fields:
            attachment.save(update_fields=update_fields)

    request.session.pop(CHECKOUT_SOUL_KEY, None)
