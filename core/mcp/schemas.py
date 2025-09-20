"""Pydantic schemas shared by the MCP sigil resolver."""

from __future__ import annotations

from typing import Dict, List, Mapping, MutableMapping

from pydantic import BaseModel, ConfigDict, Field

ContextAssignments = Mapping[str, str | int | None]
MutableContextAssignments = MutableMapping[str, str | int | None]


class ResolveOptions(BaseModel):
    """Optional switches supported by the ``resolveSigils`` tool."""

    model_config = ConfigDict(populate_by_name=True)

    skip_unknown: bool = Field(
        default=False,
        alias="skipUnknown",
        description="Drop unresolved sigils from the output",
    )


class ResolveSigilsPayload(BaseModel):
    """Incoming payload for the ``resolveSigils`` tool."""

    model_config = ConfigDict(populate_by_name=True)

    text: str
    context: Dict[str, str | int | None] | None = None
    options: ResolveOptions | None = None


class ResolveSigilsResponse(BaseModel):
    """Structured response returned by ``resolveSigils``."""

    model_config = ConfigDict(populate_by_name=True)

    resolved: str
    metadata: Dict[str, List[str]] = Field(default_factory=dict)


class SigilRootDescription(BaseModel):
    """Metadata describing a known ``SigilRoot`` entry."""

    model_config = ConfigDict(populate_by_name=True)

    prefix: str
    context_type: str = Field(alias="contextType")
    model: str | None = None
    fields: List[str] = Field(default_factory=list)


class SetContextPayload(BaseModel):
    """Payload for ``setContext`` calls."""

    model_config = ConfigDict(populate_by_name=True)

    context: Dict[str, str | int | None] = Field(default_factory=dict)


class SetContextResponse(BaseModel):
    """Response emitted after storing session context."""

    model_config = ConfigDict(populate_by_name=True)

    stored: List[str] = Field(
        default_factory=list, description="Model labels retained in the session context"
    )
