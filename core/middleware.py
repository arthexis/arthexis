from django.contrib.contenttypes.models import ContentType
from .models import AdminHistory


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
