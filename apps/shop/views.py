"""Public storefront views for catalog browsing, checkout, and tracking."""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from apps.shop.forms import AddToCartForm, CheckoutForm, OrderTrackingForm
from apps.shop.models import Shop, ShopOrder, ShopOrderItem, ShopProduct
from apps.shop.services import CartError, ShopCart


class ShopListView(ListView):
    """Display all active shops."""

    model = Shop
    context_object_name = "shops"
    template_name = "shop/shop_list.html"

    def get_queryset(self):
        """Return active storefronts sorted by name."""

        return Shop.objects.filter(is_active=True)


class ShopDetailView(View):
    """Render shop catalog and cart summary."""

    template_name = "shop/shop_detail.html"

    def get(self, request: HttpRequest, slug: str) -> HttpResponse:
        """Render product listing for a single shop."""

        shop = get_object_or_404(Shop, slug=slug, is_active=True)
        cart = ShopCart(request, shop)
        context = {
            "shop": shop,
            "products": shop.products.filter(is_active=True),
            "cart_lines": cart.lines(),
            "cart_subtotal": cart.subtotal(),
            "add_to_cart_form": AddToCartForm(),
        }
        return render(request, self.template_name, context)


class AddToCartView(View):
    """Handle adding a product to a shop cart."""

    def post(self, request: HttpRequest, slug: str, product_id: int) -> HttpResponse:
        """Add a selected product to cart and redirect to shop detail."""

        shop = get_object_or_404(Shop, slug=slug, is_active=True)
        product = get_object_or_404(ShopProduct, pk=product_id, shop=shop, is_active=True)
        form = AddToCartForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Could not add item to cart.")
            return redirect("shop-detail", slug=slug)

        cart = ShopCart(request, shop)
        try:
            cart.add(product, form.cleaned_data["quantity"])
        except CartError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Added {product.name} to cart.")
        return redirect("shop-detail", slug=slug)


class CartView(View):
    """Render cart detail and checkout form."""

    template_name = "shop/cart.html"

    def get(self, request: HttpRequest, slug: str) -> HttpResponse:
        """Display a cart and checkout inputs."""

        shop = get_object_or_404(Shop, slug=slug, is_active=True)
        cart = ShopCart(request, shop)
        context = {
            "shop": shop,
            "cart_lines": cart.lines(),
            "cart_subtotal": cart.subtotal(),
            "checkout_form": CheckoutForm(),
        }
        return render(request, self.template_name, context)


class CheckoutView(View):
    """Create order records from session cart lines."""

    @transaction.atomic
    def post(self, request: HttpRequest, slug: str) -> HttpResponse:
        """Persist checkout data, create order, and clear cart."""

        shop = get_object_or_404(Shop, slug=slug, is_active=True)
        cart = ShopCart(request, shop)
        lines = cart.lines()
        if not lines:
            messages.error(request, "Your cart is empty.")
            return redirect("shop-cart", slug=slug)

        form = CheckoutForm(request.POST)
        if not form.is_valid():
            context = {
                "shop": shop,
                "cart_lines": lines,
                "cart_subtotal": cart.subtotal(),
                "checkout_form": form,
            }
            return render(request, "shop/cart.html", context, status=400)

        subtotal = cart.subtotal()
        shipping_cost = Decimal("0.00")
        order = form.save(commit=False)
        order.shop = shop
        order.status = ShopOrder.Status.PENDING
        order.subtotal = subtotal
        order.shipping_cost = shipping_cost
        order.total = subtotal + shipping_cost
        order.currency = lines[0].product.currency if lines else "EUR"
        order.save()

        for line in lines:
            ShopOrderItem.objects.create(
                order=order,
                product=line.product,
                product_name=line.product.name,
                quantity=line.quantity,
                unit_price=line.product.unit_price,
                line_total=line.line_total,
            )

        order.sync_to_odoo()
        cart.clear()
        messages.success(request, f"Order #{order.pk} created successfully.")
        return redirect("shop-order-confirmation", slug=slug, order_id=order.pk)


class OrderConfirmationView(View):
    """Show completed order details after checkout."""

    template_name = "shop/order_confirmation.html"

    def get(self, request: HttpRequest, slug: str, order_id: int) -> HttpResponse:
        """Display the created order and line items."""

        shop = get_object_or_404(Shop, slug=slug, is_active=True)
        order = get_object_or_404(ShopOrder, pk=order_id, shop=shop)
        return render(request, self.template_name, {"shop": shop, "order": order})


class OrderTrackingView(View):
    """Lookup and display an order status using id and email."""

    template_name = "shop/order_tracking.html"

    def get(self, request: HttpRequest, slug: str) -> HttpResponse:
        """Render tracking form and optional order results."""

        shop = get_object_or_404(Shop, slug=slug, is_active=True)
        form = OrderTrackingForm(request.GET or None)
        order = None
        if request.GET and form.is_valid():
            order = ShopOrder.objects.filter(
                pk=form.cleaned_data["order_id"],
                shop=shop,
                customer_email=form.cleaned_data["customer_email"],
            ).first()
            if order is None:
                raise Http404("Order not found for the provided information.")

        return render(request, self.template_name, {"shop": shop, "form": form, "order": order})
