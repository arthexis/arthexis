from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.souls.views import attach_soul_to_order_items

from .forms import CartQuantityForm, CheckoutForm
from .models import Shop, ShopOrder, ShopOrderItem, ShopProduct
from .services import CartValidationError, calculate_cart_total, serialize_product_for_cart

CART_SESSION_KEY = "shop_cart"


def _get_cart(request: HttpRequest) -> dict:
    """Return the mutable cart dictionary from session state."""

    return request.session.setdefault(CART_SESSION_KEY, {})


def _save_cart(request: HttpRequest, cart: dict) -> None:
    """Persist cart back to session and mark session dirty."""

    request.session[CART_SESSION_KEY] = cart
    request.session.modified = True


def _require_shop_open(shop: Shop) -> None:
    """Raise CartValidationError when a shop is closed for the current local time."""

    if not shop.is_active:
        raise CartValidationError("This shop is currently unavailable.")

    now = timezone.localtime()
    if not shop.is_open_at(now.time()):
        raise CartValidationError("This shop is currently closed and cannot accept orders.")


def _resolve_cart_products(entries: list[dict]) -> tuple[Shop, dict[int, ShopProduct]]:
    """Resolve cart entries to active products from a single shop."""

    product_ids = [entry.get("product_id") for entry in entries]
    products = {
        product.id: product
        for product in ShopProduct.objects.select_related("shop").filter(id__in=product_ids, is_active=True)
    }

    if len(products) != len(product_ids):
        raise CartValidationError("Some cart items are no longer available. Please review your cart.")

    shop_ids = {product.shop_id for product in products.values()}
    if len(shop_ids) > 1:
        raise CartValidationError("Your cart contains items from multiple shops. Please update your cart.")

    shop = next(iter(products.values())).shop

    return shop, products


@require_GET
def shop_index(request: HttpRequest) -> HttpResponse:
    """Render shops currently open and include closure timing hints when relevant."""

    now = timezone.localtime()
    active_shops = list(Shop.objects.filter(is_active=True).prefetch_related("products"))

    open_shops = [shop for shop in active_shops if shop.is_open_at(now.time())]
    next_opening_candidates = [
        shop.next_opening_datetime(now)
        for shop in active_shops
        if shop.has_business_hours() and not shop.is_open_at(now.time())
    ]
    next_opening_candidates = [candidate for candidate in next_opening_candidates if candidate is not None]

    next_opening_at = min(next_opening_candidates) if next_opening_candidates else None
    next_opening_same_day = False
    next_opening_display = None
    if next_opening_at is not None:
        localized_next_opening = next_opening_at.astimezone(now.tzinfo)
        next_opening_same_day = localized_next_opening.date() == now.date()
        next_opening_display = (
            localized_next_opening.strftime("%H:%M")
            if next_opening_same_day
            else localized_next_opening.strftime("%a, %d %b %H:%M")
        )

    context = {
        "shops": open_shops,
        "all_shops_closed_for_time": bool(active_shops and not open_shops and next_opening_candidates),
        "next_opening_at": next_opening_at,
        "next_opening_same_day": next_opening_same_day,
        "next_opening_display": next_opening_display,
    }
    return render(request, "shop/index.html", context)


@require_GET
def cart_detail(request: HttpRequest) -> HttpResponse:
    """Render current cart state."""

    cart = _get_cart(request)
    entries = list(cart.values())
    total = calculate_cart_total(entries)
    return render(request, "shop/cart.html", {"entries": entries, "total": total})


@require_POST
def add_to_cart(request: HttpRequest, shop_slug: str, product_id: int) -> HttpResponse:
    """Add a product to the shopping cart and redirect back to the shop page."""

    shop = get_object_or_404(Shop, slug=shop_slug, is_active=True)
    product = get_object_or_404(ShopProduct, id=product_id, shop=shop, is_active=True)

    try:
        _require_shop_open(shop)
    except CartValidationError as exc:
        messages.error(request, str(exc))
        return redirect("shop:index")

    try:
        qty = int(request.POST.get("quantity", "1"))
    except ValueError as exc:
        raise Http404("Invalid quantity.") from exc

    cart = _get_cart(request)
    if cart:
        first_item = next(iter(cart.values()))
        if first_item.get("shop_id") != product.shop_id:
            cart.clear()
            messages.warning(
                request,
                "Your cart was cleared because it contained items from a different shop.",
            )

    existing = cart.get(str(product.id))
    next_qty = qty + int(existing["quantity"]) if existing else qty

    try:
        cart[str(product.id)] = serialize_product_for_cart(product, next_qty)
    except CartValidationError as exc:
        messages.error(request, str(exc))
    else:
        _save_cart(request, cart)
        messages.success(request, f"Added {product.name} to cart.")

    return redirect("shop:index")


@require_POST
def update_cart_item(request: HttpRequest, product_id: int) -> HttpResponse:
    """Update quantity for an item, or remove when quantity is zero."""

    form = CartQuantityForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Quantity must be between 0 and 999.")
        return redirect("shop:cart")

    cart = _get_cart(request)
    key = str(product_id)
    if key not in cart:
        messages.warning(request, "Cart item no longer exists.")
        return redirect("shop:cart")

    qty = form.cleaned_data["quantity"]
    if qty == 0:
        cart.pop(key, None)
    else:
        product = get_object_or_404(ShopProduct, id=product_id, is_active=True)
        try:
            cart[key] = serialize_product_for_cart(product, qty)
        except CartValidationError as exc:
            messages.error(request, str(exc))
            return redirect("shop:cart")

    _save_cart(request, cart)
    return redirect("shop:cart")


@require_http_methods(["GET", "POST"])
def checkout(request: HttpRequest) -> HttpResponse:
    """Collect shipping details and convert cart session data into an order."""

    cart = _get_cart(request)
    entries = list(cart.values())
    if not entries:
        messages.warning(request, "Your cart is empty.")
        return redirect("shop:index")

    if request.method == "GET":
        form = CheckoutForm()
        return render(
            request,
            "shop/checkout.html",
            {"form": form, "entries": entries, "total": calculate_cart_total(entries)},
        )

    form = CheckoutForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "shop/checkout.html",
            {"form": form, "entries": entries, "total": calculate_cart_total(entries)},
        )

    try:
        shop, products = _resolve_cart_products(entries)
        _require_shop_open(shop)
    except CartValidationError as exc:
        messages.error(request, str(exc))
        return redirect("shop:cart")

    with transaction.atomic():
        order = ShopOrder.objects.create(
            shop=shop,
            customer_name=form.cleaned_data["customer_name"],
            customer_email=form.cleaned_data["customer_email"],
            shipping_address_line1=form.cleaned_data["shipping_address_line1"],
            shipping_address_line2=form.cleaned_data["shipping_address_line2"],
            shipping_city=form.cleaned_data["shipping_city"],
            shipping_postal_code=form.cleaned_data["shipping_postal_code"],
            shipping_country=form.cleaned_data["shipping_country"],
            payment_provider=shop.default_payment_provider,
        )

        total = Decimal("0.00")
        created_items = []
        for entry in entries:
            unit_price = Decimal(entry["unit_price"])
            quantity = int(entry["quantity"])
            line_total = unit_price * quantity
            total += line_total
            product = products[entry["product_id"]]
            created_item = ShopOrderItem.objects.create(
                order=order,
                product=product,
                odoo_product=product.odoo_product,
                product_name=entry["name"],
                sku=entry.get("sku", ""),
                unit_price=unit_price,
                quantity=quantity,
                line_total=line_total,
            )
            created_items.append(created_item)

        attach_soul_to_order_items(request=request, order_items=created_items)

        order.total_amount = total
        order.save(update_fields=["total_amount", "updated_at"])

    _save_cart(request, {})
    messages.success(request, "Order placed successfully.")
    return redirect(reverse("shop:order_tracking", kwargs={"tracking_token": order.tracking_token}))


@require_GET
def order_tracking(request: HttpRequest, tracking_token: str) -> HttpResponse:
    """Show order status and shipping metadata using tracking token."""

    order = get_object_or_404(ShopOrder.objects.prefetch_related("items"), tracking_token=tracking_token)
    return render(request, "shop/order_tracking.html", {"order": order})
