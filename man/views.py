import base64
from types import SimpleNamespace

from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.test import RequestFactory
from django.utils.translation import gettext as _

from pages.utils import landing

from .models import UserManual


def _admin_context(request):
    context = admin.site.each_context(request)
    if not context.get("has_permission"):
        rf = RequestFactory()
        mock_request = rf.get(request.path)
        mock_request.user = SimpleNamespace(
            is_active=True,
            is_staff=True,
            is_superuser=True,
            has_perm=lambda perm, obj=None: True,
            has_module_perms=lambda app_label: True,
        )
        context["available_apps"] = admin.site.get_app_list(mock_request)
        context["has_permission"] = True
    return context


def admin_manual_detail(request, slug):
    manual = get_object_or_404(UserManual, slug=slug)
    context = _admin_context(request)
    context["manual"] = manual
    return render(request, "admin_doc/manual_detail.html", context)


def manual_pdf(request, slug):
    manual = get_object_or_404(UserManual, slug=slug)
    pdf_data = base64.b64decode(manual.content_pdf)
    response = HttpResponse(pdf_data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{manual.slug}.pdf"'
    return response


def admin_manual_list(request):
    manuals = UserManual.objects.all()
    context = _admin_context(request)
    context["manuals"] = manuals
    return render(request, "admin_doc/manuals.html", context)


@landing(_("Manuals"))
def manual_list(request):
    manuals = UserManual.objects.all()
    return render(request, "man/manual_list.html", {"manuals": manuals})


def manual_detail(request, slug):
    manual = get_object_or_404(UserManual, slug=slug)
    return render(request, "man/manual_detail.html", {"manual": manual})
