from django.urls import path
from . import views

app_name = "user_manuals"

urlpatterns = [
    path("", views.manual_list, name="list"),
    path("<slug:slug>/", views.manual_html, name="manual-html"),
    path("<slug:slug>/pdf/", views.manual_pdf, name="manual-pdf"),
]
