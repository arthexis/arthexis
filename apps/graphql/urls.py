"""URL routing for GraphQL endpoints."""

from django.contrib.auth.decorators import login_required
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from graphene_django.views import GraphQLView

from .schema import schema

app_name = "graphql"

urlpatterns = [
    path(
        "",
        login_required(csrf_exempt(GraphQLView.as_view(schema=schema, graphiql=False))),
        name="endpoint",
    ),
]
