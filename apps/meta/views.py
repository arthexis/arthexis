"""Meta webhook views removed with chat bridge retirement."""

from django.http import HttpRequest, HttpResponse


def whatsapp_webhook(request: HttpRequest, route_key: str) -> HttpResponse:
    """Return gone for retired WhatsApp bridge webhook endpoint."""

    return HttpResponse("Gone", status=410)
