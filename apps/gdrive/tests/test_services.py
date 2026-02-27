from __future__ import annotations

from types import MethodType

import pytest
from django.contrib.auth import get_user_model

from apps.gdrive.models import GoogleAccount, GoogleSheet, GoogleSheetColumn
from apps.gdrive.services import GoogleSheetsGateway

