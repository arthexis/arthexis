from django.urls import path

from apps.certs import views


urlpatterns = [
    path("trust/", views.trust_certificate, name="certs-trust"),
    path("trust/download/", views.trust_certificate_download, name="certs-trust-download"),
]
