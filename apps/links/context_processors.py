from django.db.utils import DatabaseError

from .models import get_or_create_short_url


def share_short_url(request):
    if request is None:
        return {"share_short_url": ""}
    try:
        target_url = request.build_absolute_uri(request.path)
        short_url = get_or_create_short_url(target_url)
    except DatabaseError:
        return {"share_short_url": ""}
    if not short_url:
        return {"share_short_url": ""}
    return {"share_short_url": request.build_absolute_uri(short_url.redirect_path())}
