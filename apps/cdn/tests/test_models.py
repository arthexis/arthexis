"""Tests for CDN provider configuration validation."""

import pytest
from django.db import IntegrityError

from apps.cdn.models import CDNConfiguration

