import asyncio
import base64
import contextlib
import json
import time as time_module
import uuid
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests
from asgiref.sync import async_to_sync
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.utils import quote
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.db.models.deletion import ProtectedError
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import formats, timezone, translation
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html, format_html_join
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from requests import RequestException

from apps.cards.models import RFID as CoreRFID
from apps.core.admin import OwnableAdminMixin, SaveBeforeChangeAction
from apps.core.form_fields import SchedulePeriodsField
from apps.energy.models import EnergyTariff
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from .. import store
from ..models import (
    CertificateOperation,
    CertificateRequest,
    CertificateStatusCheck,
    Charger,
    ChargerConfiguration,
    ChargerLogRequest,
    ChargingProfile,
    ChargingProfileDispatch,
    ChargingSchedule,
    ConfigurationKey,
    CPFirmware,
    CPFirmwareDeployment,
    CPNetworkProfile,
    CPNetworkProfileDeployment,
    CPReservation,
    DataTransferMessage,
    InstalledCertificate,
    MeterValue,
    MonitoringReport,
    MonitoringRule,
    PowerProjection,
    SecurityEvent,
    StationModel,
    StationModelConfigurationGuide,
    StationModelConfigurationGuideStep,
    Transaction,
    TrustAnchor,
    Variable,
)
from ..status_display import ERROR_OK_VALUES, STATUS_BADGE_MAP
from ..status_resets import clear_stale_cached_statuses
from ..transactions_io import (
    export_transactions,
)
from ..transactions_io import (
    import_transactions as import_transactions_data,
)
from ..views import _charger_state, _live_sessions

# Ensure gettext alias is available when using wildcard imports.
__all__ = [name for name in globals().keys() if not name.startswith("_")] + ["_"]
