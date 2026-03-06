"""URL declarations for storefront pages."""

from django.urls import path

from apps.shop.views import (
    AddToCartView,
    CartView,
    CheckoutView,
    OrderConfirmationView,
    OrderTrackingView,
    ShopDetailView,
    ShopListView,
)

urlpatterns = [
    path("shop/", ShopListView.as_view(), name="shop-list"),
    path("shop/<slug:slug>/", ShopDetailView.as_view(), name="shop-detail"),
    path("shop/<slug:slug>/cart/", CartView.as_view(), name="shop-cart"),
    path("shop/<slug:slug>/cart/add/<int:product_id>/", AddToCartView.as_view(), name="shop-add-to-cart"),
    path("shop/<slug:slug>/checkout/", CheckoutView.as_view(), name="shop-checkout"),
    path("shop/<slug:slug>/orders/<int:order_id>/", OrderConfirmationView.as_view(), name="shop-order-confirmation"),
    path("shop/<slug:slug>/track/", OrderTrackingView.as_view(), name="shop-order-track"),
]
