from django.urls import path

from website.views import app_index

from . import views

app_name = "integrations"

urlpatterns = [
    path("", app_index, {"module": __name__}, name="index"),
    path("register/", views.register, name="register"),
    path("post/", views.post, name="post"),
    path("domain-post/", views.domain_post, name="domain-post"),
    path("odoo/test/<int:pk>/", views.test_connection, name="odoo-test"),
]
