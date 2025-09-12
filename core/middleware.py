from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from django.urls import resolve

from nodes.models import Node, NodeRole

from .models import AdminHistory
from .sigil_context import set_context, clear_context


class AdminHistoryMiddleware:
    """Log recently visited admin changelists for each user."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = getattr(request, "resolver_match", None)
        if (
            request.user.is_authenticated
            and request.user.is_staff
            and request.method == "GET"
            and match
            and match.url_name
            and match.url_name.endswith("_changelist")
            and response.status_code == 200
        ):
            parts = request.path.strip("/").split("/")
            if len(parts) >= 3:
                app_label, model_name = parts[1], parts[2]
                content_type = ContentType.objects.get_by_natural_key(
                    app_label, model_name
                )
                AdminHistory.objects.update_or_create(
                    user=request.user,
                    url=request.get_full_path(),
                    defaults={"content_type": content_type},
                )
        return response


class SigilContextMiddleware:
    """Capture model instance identifiers from resolved views."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        context = {}
        if request.user.is_authenticated:
            context[get_user_model()] = request.user.pk
        try:
            site = Site.objects.get_current(request)
            context[Site] = site.pk
        except Exception:
            pass
        if hasattr(request, "node") and getattr(request, "node", None):
            context[Node] = request.node.pk
        if hasattr(request, "role") and getattr(request, "role", None):
            context[NodeRole] = request.role.pk
        try:
            match = resolve(request.path_info)
        except Exception:  # pragma: no cover - resolution errors
            match = None
        if match and hasattr(match, "func"):
            view = match.func
            model = None
            if hasattr(view, "view_class"):
                view_class = view.view_class
                model = getattr(view_class, "model", None)
                if model is None:
                    queryset = getattr(view_class, "queryset", None)
                    if queryset is not None:
                        model = queryset.model
            if model is not None:
                pk = match.kwargs.get("pk") or match.kwargs.get("id")
                if pk is not None:
                    context[model] = pk
                for field in model._meta.fields:
                    if field.is_relation:
                        for key in (field.name, field.attname):
                            if key in match.kwargs:
                                context[field.related_model] = match.kwargs[key]
                                break
        set_context(context)
        try:
            return self.get_response(request)
        finally:
            clear_context()
