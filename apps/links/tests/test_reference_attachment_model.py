"""Tests for the generic ReferenceAttachment model."""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError

from apps.links.models import Reference, ReferenceAttachment

pytestmark = pytest.mark.django_db

