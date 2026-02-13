"""Views for URL-based Google Sheet discovery."""

from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from .forms import SheetDiscoveryForm
from .models import GoogleSheet


@staff_member_required
def discover_sheet(request: HttpRequest) -> HttpResponse:
    """Create or update tracked GoogleSheet records from URLs."""

    form = SheetDiscoveryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        sheet_url = form.cleaned_data["sheet_url"]
        drive_account = form.cleaned_data["drive_account"]
        GoogleSheet.discover_from_url(
            sheet_url=sheet_url,
            drive_account=drive_account,
            is_public=drive_account is None,
        )
        return redirect("admin:google_googlesheet_changelist")

    return render(request, "google/sheets/discover.html", {"form": form})
