from django.contrib.sites.shortcuts import get_current_site
import socket

from .active_app import set_active_app


class ActiveAppMiddleware:
    """Store the current app based on the request's site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        site = get_current_site(request)
        active = site.name or "website"
        set_active_app(active)
        request.active_app = active
        try:
            response = self.get_response(request)
        finally:
            set_active_app(socket.gethostname())
        return response
