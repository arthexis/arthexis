from django.urls import path
from . import views

urlpatterns = [
    path('rfid-login/', views.rfid_login, name='rfid-login'),
    path('products/', views.product_list, name='product-list'),
    path('subscribe/', views.add_subscription, name='add-subscription'),
    path('list/', views.subscription_list, name='subscription-list'),
]
