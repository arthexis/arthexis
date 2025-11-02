from __future__ import annotations

import graphene
from django.contrib.auth.models import AnonymousUser
from graphql import GraphQLError

REQUIRED_PERMISSIONS = (
    "ocpp.view_transaction",
    "ocpp.view_metervalue",
)


class EnergyExportStatus(graphene.ObjectType):
    """Basic readiness indicator for the GraphQL endpoint."""

    ready = graphene.Boolean(required=True, description="Whether the endpoint is accepting requests.")
    message = graphene.String(required=True, description="Human readable status description.")


class Query(graphene.ObjectType):
    """Root GraphQL query for energy exports."""

    energy_export_status = graphene.Field(
        EnergyExportStatus,
        description="Return readiness information for the energy export GraphQL endpoint.",
    )

    @staticmethod
    def resolve_energy_export_status(root, info):  # noqa: D401  # Graphene signature
        """Resolve the energy export status after enforcing permissions."""

        _assert_has_permissions(getattr(info.context, "user", None))
        return EnergyExportStatus(ready=True, message="Energy export GraphQL endpoint is online.")


schema = graphene.Schema(query=Query)


def _assert_has_permissions(user) -> None:
    """Ensure ``user`` can access the energy export GraphQL endpoint."""

    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        raise GraphQLError("Authentication required.")

    missing = [perm for perm in REQUIRED_PERMISSIONS if not user.has_perm(perm)]
    if missing:
        raise GraphQLError("You do not have permission to access energy export data.")
