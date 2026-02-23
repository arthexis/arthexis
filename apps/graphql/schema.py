"""GraphQL schema for public charger chart queries."""

from __future__ import annotations

import graphene
from django.core.exceptions import ObjectDoesNotExist
from graphql import GraphQLError

from .services import ChargerAccessDeniedError, build_charger_chart_payload


class ChargerChartDatasetType(graphene.ObjectType):
    """One chart dataset for a charger or connector."""

    label = graphene.String(required=True)
    values = graphene.List(graphene.Float)
    connector_id = graphene.Int(name="connectorId")


class ChargerChartType(graphene.ObjectType):
    """Chart payload structure consumed by the public charger UI."""

    labels = graphene.List(graphene.String, required=True)
    datasets = graphene.List(ChargerChartDatasetType, required=True)


class Query(graphene.ObjectType):
    """Root query type for public charger graph data."""

    charger_chart = graphene.Field(
        ChargerChartType,
        cid=graphene.String(required=True),
        connector=graphene.String(),
        session_id=graphene.String(name="sessionId"),
        description="Return chart data for charger status graph rendering.",
    )

    def resolve_charger_chart(self, info, cid: str, connector: str | None = None, session_id: str | None = None):
        """Resolve chart data for the requested charger context."""

        request = info.context
        if request.user.is_anonymous:
            raise GraphQLError("Authentication required")
        try:
            payload = build_charger_chart_payload(
                user=request.user,
                cid=cid,
                connector=connector,
                session_id=session_id,
            )
        except ChargerAccessDeniedError as exc:
            raise GraphQLError(str(exc)) from exc
        except ObjectDoesNotExist as exc:
            raise GraphQLError(str(exc)) from exc
        return ChargerChartType(
            labels=payload["labels"],
            datasets=[
                ChargerChartDatasetType(
                    label=item["label"],
                    values=item["values"],
                    connector_id=item.get("connector_id"),
                )
                for item in payload["datasets"]
            ],
        )


schema = graphene.Schema(query=Query)
