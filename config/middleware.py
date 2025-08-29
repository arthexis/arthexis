from utils.sites import get_site
import socket
from nodes.models import Node

from .active_app import set_active_app


class ActiveAppMiddleware:
    """Store the current app based on the request's site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        site = get_site(request)
        node = Node.get_local()
        role_name = node.role.name if node and node.role else "Terminal"
        active = site.name or role_name
        set_active_app(active)
        request.active_app = active
        try:
            response = self.get_response(request)
        finally:
            set_active_app(socket.gethostname())
        return response
