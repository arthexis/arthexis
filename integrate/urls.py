from django.urls import path

from . import views

app_name = "integrate"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("post/", views.post, name="post"),
    path("domain-post/", views.domain_post, name="domain-post"),
    path("odoo/test/<int:pk>/", views.test_connection, name="odoo-test"),
]
