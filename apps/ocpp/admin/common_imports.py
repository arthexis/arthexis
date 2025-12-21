import asyncio
import base64
import contextlib
import json
import time as time_module
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
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
from django.db.models import Q, Max
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import formats, timezone, translation
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html, format_html_join
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _, ngettext
from requests import RequestException

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel
from apps.core.admin import SaveBeforeChangeAction
from apps.energy.models import EnergyTariff
from apps.cards.models import RFID as CoreRFID
from apps.core.form_fields import SchedulePeriodsField
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node

from ..models import (
    Charger,
    ChargingProfile,
    ChargingProfileDispatch,
    ChargingSchedule,
    PowerProjection,
    ChargerConfiguration,
    ConfigurationKey,
    Simulator,
    MeterValue,
    StationModel,
    Transaction,
    DataTransferMessage,
    CPReservation,
    CPFirmware,
    CPFirmwareDeployment,
    CPNetworkProfile,
    CPNetworkProfileDeployment,
    SecurityEvent,
    ChargerLogRequest,
    CPForwarder,
    CertificateRequest,
    CertificateStatusCheck,
    CertificateOperation,
    InstalledCertificate,
    TrustAnchor,
    Variable,
    MonitoringRule,
    MonitoringReport,
)
from ..simulator import ChargePointSimulator
from .. import store
from ..transactions_io import export_transactions, import_transactions as import_transactions_data
from ..status_display import STATUS_BADGE_MAP, ERROR_OK_VALUES
from ..status_resets import clear_stale_cached_statuses
from ..views import _charger_state, _live_sessions

# Ensure gettext alias is available when using wildcard imports.
__all__ = [name for name in globals().keys() if not name.startswith("_")] + ["_"]
