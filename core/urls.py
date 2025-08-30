from django.urls import path

from . import views

urlpatterns = [
    path("rfid-login/", views.rfid_login, name="rfid-login"),
    path("rfids/", views.rfid_batch, name="rfid-batch"),
    path("products/", views.product_list, name="product-list"),
    path("subscribe/", views.add_subscription, name="add-subscription"),
    path("list/", views.subscription_list, name="subscription-list"),
    path(
        "releases/<int:pk>/<str:action>/",
        views.release_progress,
        name="release-progress",
    ),
]
