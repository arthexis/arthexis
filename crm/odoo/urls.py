from django.urls import path

from . import views

urlpatterns = [
    path('test/<int:pk>/', views.test_connection, name='odoo-test'),
]
