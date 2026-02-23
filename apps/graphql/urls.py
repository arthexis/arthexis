"""URL routing for GraphQL endpoints."""

from functools import wraps

from django.http import JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from graphene_django.views import GraphQLView

from .schema import schema



def graphql_login_required(view_func):
    """Return a JSON 401 for unauthenticated GraphQL requests."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"errors": [{"message": "Authentication required"}]}, status=401)
        return view_func(request, *args, **kwargs)

    wrapper.csrf_exempt = True
    return wrapper


app_name = "graphql"

urlpatterns = [
    path(
        "",
        graphql_login_required(csrf_exempt(GraphQLView.as_view(schema=schema, graphiql=False))),
        name="endpoint",
    ),
]
