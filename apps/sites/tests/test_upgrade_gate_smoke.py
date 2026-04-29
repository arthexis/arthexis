from __future__ import annotations

from collections.abc import Iterable

import pytest
from django.urls import reverse

from gate_markers import gate

pytestmark = [pytest.mark.django_db, gate.upgrade]

