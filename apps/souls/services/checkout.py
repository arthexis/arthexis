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
        ShopOrderSoulAttachment.objects.get_or_create(order_item=item, soul=soul)

    request.session.pop(CHECKOUT_SOUL_KEY, None)
