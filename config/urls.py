"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.utils.translation import gettext_lazy as _

admin.site.site_header = _("Arthexis Constellation")
admin.site.site_title = _("Arthexis Constellation")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("website.urls")),
    path("nodes/", include("nodes.urls")),
    path("accounts/", include("accounts.urls")),
    path("subscriptions/", include("subscriptions.urls")),
    path("ocpp/", include("ocpp.urls")),
    path("qr/", include("qrcodes.urls")),
    path("awg/", include("awg.urls")),
    path("odoo/", include("odoo.urls")),
    path("todos/", include("todos.urls")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
