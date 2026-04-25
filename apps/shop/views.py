from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch, Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.gallery.models import GalleryImage
from apps.gallery.permissions import can_manage_gallery
from apps.souls.services import attach_soul_to_order_items

from .forms import CartQuantityForm, CheckoutForm
from .models import Shop, ShopOrder, ShopOrderItem, ShopProduct
from .services import (
    CartValidationError,
    calculate_cart_total,
    serialize_product_for_cart,
)

CART_SESSION_KEY = "shop_cart"
GALLERY_HANDOFF_SESSION_KEY = "shop_cart_gallery_image"


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


def _gallery_images_for_checkout(request: HttpRequest) -> QuerySet[GalleryImage]:
    """Return gallery images visible to current user for card customization."""

    queryset = GalleryImage.objects.select_related("media_file")
    if can_manage_gallery(request.user):
        return queryset

    visibility_filter = Q(include_in_public_gallery=True)
    if request.user.is_authenticated:
        visibility_filter |= Q(owner_user=request.user)
        visibility_filter |= Q(owner_group__in=request.user.groups.all())
        visibility_filter |= Q(shared_with_users=request.user)
    return queryset.filter(visibility_filter).distinct()


def _selected_gallery_image_for_store(request: HttpRequest) -> GalleryImage | None:
    selected_gallery_image_id = request.GET.get("gallery_image")
    if selected_gallery_image_id is None:
        stored_selection = request.session.get(GALLERY_HANDOFF_SESSION_KEY) or {}
        try:
            image_id = int(stored_selection.get("gallery_image_id"))
        except (TypeError, ValueError):
            if stored_selection:
                request.session.pop(GALLERY_HANDOFF_SESSION_KEY, None)
                request.session.modified = True
            return None

        image = _gallery_images_for_checkout(request).filter(pk=image_id).first()
        if image is None:
            request.session.pop(GALLERY_HANDOFF_SESSION_KEY, None)
            request.session.modified = True
        return image

    selected_gallery_image_id = selected_gallery_image_id.strip()
    if not selected_gallery_image_id:
        return None
    try:
        image_id = int(selected_gallery_image_id)
    except ValueError:
        return None
    image = _gallery_images_for_checkout(request).filter(pk=image_id).first()
    if image is None:
        request.session.pop(GALLERY_HANDOFF_SESSION_KEY, None)
        request.session.modified = True
        messages.error(request, "Selected gallery image is unavailable for RF card customization.")
    else:
        request.session[GALLERY_HANDOFF_SESSION_KEY] = {
            "product_id": None,
            "gallery_image_id": str(image.id),
        }
        request.session.modified = True
    return image


def _extract_card_customizations(
    request: HttpRequest,
    entries: list[dict],
    products: dict[int, ShopProduct],
    gallery_images: dict[int, GalleryImage],
) -> tuple[dict[int, dict], bool]:
    """Parse and validate optional front/back gallery image selections."""

    customizations: dict[int, dict] = {}
    has_errors = False
    for entry in entries:
        product = products[entry["product_id"]]
        if not product.supports_gallery_image_printing:
            continue

        product_id = product.id
        front_value = (request.POST.get(f"front_gallery_image_{product_id}") or "").strip()
        back_value = (request.POST.get(f"back_gallery_image_{product_id}") or "").strip()

        front_image = None
        back_image = None
        if front_value:
            try:
                front_image = gallery_images[int(front_value)]
            except (ValueError, KeyError):
                messages.error(request, f"Selected front image for {product.name} is unavailable.")
                has_errors = True

        if back_value:
            try:
                back_image = gallery_images[int(back_value)]
            except (ValueError, KeyError):
                messages.error(request, f"Selected back image for {product.name} is unavailable.")
                has_errors = True

        sides_selected = 0
        if front_image is not None:
            sides_selected += 1
        if back_image is not None:
            sides_selected += 1
        surcharge_per_unit = product.gallery_image_print_price * sides_selected
        customizations[product_id] = {
            "front": front_image,
            "back": back_image,
            "surcharge_per_unit": surcharge_per_unit,
        }

    return customizations, has_errors


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

    selected_gallery_image = _selected_gallery_image_for_store(request)
    context = {
        "shops": open_shops,
        "all_shops_closed_for_time": bool(active_shops and not open_shops and next_opening_candidates),
        "next_opening_at": next_opening_at,
        "next_opening_same_day": next_opening_same_day,
        "next_opening_display": next_opening_display,
        "selected_gallery_image": selected_gallery_image,
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
    existing_handoff = request.session.get(GALLERY_HANDOFF_SESSION_KEY) or {}
    next_qty = qty + int(existing["quantity"]) if existing else qty

    try:
        cart[str(product.id)] = serialize_product_for_cart(product, next_qty)
    except CartValidationError as exc:
        messages.error(request, str(exc))
    else:
        _save_cart(request, cart)
        selected_gallery_image_id = (request.POST.get("gallery_image") or "").strip()
        if product.supports_gallery_image_printing:
            if selected_gallery_image_id:
                try:
                    gallery_image_id = int(selected_gallery_image_id)
                except ValueError:
                    gallery_image_id = None
                if gallery_image_id and _gallery_images_for_checkout(request).filter(pk=gallery_image_id).exists():
                    request.session[GALLERY_HANDOFF_SESSION_KEY] = {
                        "product_id": product.id,
                        "gallery_image_id": str(gallery_image_id),
                    }
                    request.session.modified = True
                else:
                    request.session.pop(GALLERY_HANDOFF_SESSION_KEY, None)
                    request.session.modified = True
                    messages.error(request, "Selected gallery image is unavailable for RF card customization.")
            else:
                existing_gallery_image_id = existing_handoff.get("gallery_image_id")
                try:
                    existing_gallery_image_id_int = int(existing_gallery_image_id)
                except (TypeError, ValueError):
                    existing_gallery_image_id_int = None
                is_valid_existing_handoff = (
                    existing_gallery_image_id_int is not None
                    and _gallery_images_for_checkout(request).filter(pk=existing_gallery_image_id_int).exists()
                )
                should_preserve_existing_handoff = (
                    existing is not None
                    and existing_handoff.get("product_id") == product.id
                    and is_valid_existing_handoff
                )
                should_apply_pending_handoff = existing_handoff.get("product_id") is None and is_valid_existing_handoff
                if should_apply_pending_handoff:
                    request.session[GALLERY_HANDOFF_SESSION_KEY] = {
                        "product_id": product.id,
                        "gallery_image_id": str(existing_gallery_image_id_int),
                    }
                    request.session.modified = True
                elif should_preserve_existing_handoff:
                    request.session.modified = True
                elif existing_handoff and existing_handoff.get("product_id") in (None, product.id):
                    request.session.pop(GALLERY_HANDOFF_SESSION_KEY, None)
                    request.session.modified = True
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

    base_total = calculate_cart_total(entries)
    try:
        shop, products = _resolve_cart_products(entries)
    except CartValidationError as exc:
        messages.error(request, str(exc))
        return redirect("shop:cart")

    customization_entries = [
        {"entry": entry, "product": products[entry["product_id"]], "selected_front": "", "selected_back": ""}
        for entry in entries
        if products[entry["product_id"]].supports_gallery_image_printing
    ]
    gallery_images: list[GalleryImage] = []
    gallery_image_map: dict[int, GalleryImage] = {}
    if customization_entries:
        gallery_images = list(_gallery_images_for_checkout(request).order_by("title", "id"))
        gallery_image_map = {image.id: image for image in gallery_images}
    stored_gallery_selection = request.session.get(GALLERY_HANDOFF_SESSION_KEY) or {}
    if request.method == "GET" and stored_gallery_selection and customization_entries:
        stored_product_id = stored_gallery_selection.get("product_id")
        stored_gallery_image_id = stored_gallery_selection.get("gallery_image_id")
        try:
            stored_gallery_image_id_int = int(stored_gallery_image_id)
        except (TypeError, ValueError):
            stored_gallery_image_id_int = None
        if stored_gallery_image_id_int in gallery_image_map:
            for entry_data in customization_entries:
                if entry_data["product"].id == stored_product_id:
                    entry_data["selected_front"] = str(stored_gallery_image_id_int)
                    break

    if request.method == "GET":
        form = CheckoutForm()
        return render(
            request,
            "shop/checkout.html",
            {
                "form": form,
                "entries": entries,
                "products": products,
                "gallery_images": gallery_images,
                "customization_entries": customization_entries,
                "total": base_total,
            },
        )

    form = CheckoutForm(request.POST)
    for entry_data in customization_entries:
        product = entry_data["product"]
        entry_data["selected_front"] = (request.POST.get(f"front_gallery_image_{product.id}") or "").strip()
        entry_data["selected_back"] = (request.POST.get(f"back_gallery_image_{product.id}") or "").strip()
    customizations, customization_errors = _extract_card_customizations(
        request,
        entries,
        products,
        gallery_image_map,
    )
    if not form.is_valid() or customization_errors:
        return render(
            request,
            "shop/checkout.html",
            {
                "form": form,
                "entries": entries,
                "products": products,
                "gallery_images": gallery_images,
                "customization_entries": customization_entries,
                "total": base_total,
            },
        )

    try:
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
            product = products[entry["product_id"]]
            customization = customizations.get(
                product.id,
                {"front": None, "back": None, "surcharge_per_unit": Decimal("0.00")},
            )
            line_total = (unit_price + customization["surcharge_per_unit"]) * quantity
            total += line_total
            created_item = ShopOrderItem.objects.create(
                order=order,
                product=product,
                odoo_product=product.odoo_product,
                product_name=entry["name"],
                sku=entry.get("sku", ""),
                unit_price=unit_price,
                quantity=quantity,
                line_total=line_total,
                customization_surcharge_per_unit=customization["surcharge_per_unit"],
                front_gallery_image=customization["front"],
                back_gallery_image=customization["back"],
            )
            created_items.append(created_item)

        order.total_amount = total
        order.save(update_fields=["total_amount", "updated_at"])

    attach_soul_to_order_items(
        request=request,
        order_items=created_items,
        customer_email=order.customer_email,
    )

    request.session.pop(GALLERY_HANDOFF_SESSION_KEY, None)
    _save_cart(request, {})
    messages.success(request, "Order placed successfully.")
    return redirect(reverse("shop:order_tracking", kwargs={"tracking_token": order.tracking_token}))


@require_GET
def order_tracking(request: HttpRequest, tracking_token: str) -> HttpResponse:
    """Show order status and shipping metadata using tracking token."""

    order = get_object_or_404(
        ShopOrder.objects.prefetch_related(
            Prefetch(
                "items",
                queryset=ShopOrderItem.objects.select_related("front_gallery_image", "back_gallery_image"),
            )
        ),
        tracking_token=tracking_token,
    )
    return render(request, "shop/order_tracking.html", {"order": order})
