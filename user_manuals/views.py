import base64
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from .models import UserManual


def manual_html(request, slug):
    manual = get_object_or_404(UserManual, slug=slug)
    return HttpResponse(manual.content_html)


def manual_pdf(request, slug):
    manual = get_object_or_404(UserManual, slug=slug)
    pdf_data = base64.b64decode(manual.content_pdf)
    return HttpResponse(pdf_data, content_type="application/pdf")


def manual_list(request):
    manuals = UserManual.objects.all()
    return render(request, "user_manuals/list.html", {"manuals": manuals})
