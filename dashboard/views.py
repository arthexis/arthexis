from django.shortcuts import render

from dashboard.services import dashboard_context


def index(request):
    return render(request, "dashboard/index.html", dashboard_context())


def status_fragment(request):
    return render(request, "dashboard/_charger_status.html", dashboard_context())
