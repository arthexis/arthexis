from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.sites.middleware import SharePreviewPublicMiddleware


def test_share_preview_public_middleware_sets_anonymous_user():
    request = RequestFactory().get("/?djdt=share-preview&share_preview_public=1")
    request.user = object()

    middleware = SharePreviewPublicMiddleware(lambda req: req.user)

    response_user = middleware(request)

    assert isinstance(response_user, AnonymousUser)


def test_share_preview_public_middleware_preserves_authenticated_context():
    user = object()
    request = RequestFactory().get("/?djdt=share-preview")
    request.user = user

    middleware = SharePreviewPublicMiddleware(lambda req: req.user)

    response_user = middleware(request)

    assert response_user is user
