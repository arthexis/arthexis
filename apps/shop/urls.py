from django.urls import path

from . import views

app_name = "shop"

urlpatterns = [
    path("", views.shop_index, name="index"),
    path("cart/", views.cart_detail, name="cart"),
    path("checkout/", views.checkout, name="checkout"),
    path("<slug:shop_slug>/products/<int:product_id>/add/", views.add_to_cart, name="add_to_cart"),
    path("cart/items/<int:product_id>/", views.update_cart_item, name="update_cart_item"),
    path("orders/<uuid:tracking_token>/", views.order_tracking, name="order_tracking"),
]
