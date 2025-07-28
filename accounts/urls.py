from django.urls import path
from . import views

urlpatterns = [
    path('rfid-login/', views.rfid_login, name='rfid-login'),
]
