from django.urls import path

from website.views import app_index

from . import views

urlpatterns = [
    path("", app_index, {"module": __name__}, name="index"),
    path("rfid-login/", views.rfid_login, name="rfid-login"),
    path("rfids/", views.rfid_batch, name="rfid-batch"),
    path("products/", views.product_list, name="product-list"),
    path("subscribe/", views.add_subscription, name="add-subscription"),
    path("list/", views.subscription_list, name="subscription-list"),
]
