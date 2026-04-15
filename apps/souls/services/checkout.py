from __future__ import annotations

from django.http import HttpRequest

from apps.souls.models import ShopOrderSoulAttachment, Soul

CHECKOUT_SOUL_KEY = "shop_checkout_soul_id"


def _resolve_checkout_soul(*, request: HttpRequest, customer_email: str) -> Soul | None:
    soul_id = request.session.get(CHECKOUT_SOUL_KEY)
    if soul_id:
        soul = Soul.objects.filter(pk=soul_id).first()
        if soul:
            return soul
        request.session.pop(CHECKOUT_SOUL_KEY, None)

    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        soul = Soul.objects.filter(user=user).first()
        if soul:
            return soul

    souls = list(Soul.objects.select_related("user").filter(user__email__iexact=customer_email).order_by("id")[:2])
    if len(souls) == 1:
        return souls[0]
    return None


def attach_soul_to_order_items(*, request: HttpRequest, order_items: list, customer_email: str) -> None:
    soul = _resolve_checkout_soul(request=request, customer_email=customer_email)
    if not soul:
        return

    for item in order_items:
        product = getattr(item, "product", None)
        if not product or not product.supports_soul_seed_preload:
            continue
        ShopOrderSoulAttachment.objects.get_or_create(order_item=item, soul=soul)

    request.session.pop(CHECKOUT_SOUL_KEY, None)
