from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

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


@require_GET
def shop_index(request: HttpRequest) -> HttpResponse:
    """Render all active shops and their active products."""

    shops = Shop.objects.filter(is_active=True).prefetch_related("products")
    return render(request, "shop/index.html", {"shops": shops})


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
        qty = int(request.POST.get("quantity", "1"))
    except ValueError as exc:
        raise Http404("Invalid quantity.") from exc

    cart = _get_cart(request)
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

    shop_id = request.POST.get("shop_id")
    product_ids = [entry["product_id"] for entry in entries]
    products_by_id = ShopProduct.objects.select_related("shop").in_bulk(product_ids)
    missing_product_ids = [product_id for product_id in product_ids if product_id not in products_by_id]
    if missing_product_ids:
        messages.error(request, "Some products in your cart are no longer available. Please review your cart.")
        return redirect("shop:cart")

    products = [products_by_id[product_id] for product_id in product_ids]
    cart_shop_ids = {product.shop_id for product in products}
    if len(cart_shop_ids) != 1:
        messages.error(request, "Your cart has products from multiple shops. Please checkout one shop at a time.")
        return redirect("shop:cart")

    cart_shop_id = next(iter(cart_shop_ids))
    if shop_id:
        shop = get_object_or_404(Shop, id=shop_id, is_active=True)
        if shop.id != cart_shop_id:
            messages.error(request, "Selected shop does not match the products in your cart.")
            return redirect("shop:cart")
    else:
        shop = products[0].shop

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
        for entry in entries:
            product = products_by_id[entry["product_id"]]
            unit_price = Decimal(entry["unit_price"])
            quantity = int(entry["quantity"])
            line_total = unit_price * quantity
            total += line_total
            ShopOrderItem.objects.create(
                order=order,
                product=product,
                odoo_product_id=entry.get("odoo_product_id"),
                product_name=entry["name"],
                sku=entry.get("sku", ""),
                unit_price=unit_price,
                quantity=quantity,
                line_total=line_total,
            )

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
