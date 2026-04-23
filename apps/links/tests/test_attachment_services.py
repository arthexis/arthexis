"""Tests for generic reference attachment services."""

from __future__ import annotations

import pytest

from apps.links.models import Reference, ReferenceAttachment
from apps.links.services import (
    attach_reference,
    list_references,
    resolve_objects_by_reference,
)

pytestmark = pytest.mark.django_db

