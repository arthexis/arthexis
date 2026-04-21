"""Views for Raspberry Pi Connect integration app."""

from django.http import HttpRequest, HttpResponse


def health(request: HttpRequest) -> HttpResponse:
    """Return a minimal app health response."""

    return HttpResponse("ok")
