from __future__ import annotations

import binascii
import hashlib
import json
import os
import re
import secrets
import socket
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

from django.apps import apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import DecimalField, OuterRef, Prefetch, Q, Subquery
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from asgiref.sync import async_to_sync

from apps.base.models import Entity, EntityManager
from apps.nodes.models import Node

from apps.energy.models import CustomerAccount, EnergyTariff
from apps.maps.models import Location
from apps.links.models import Reference
from apps.cards.models import RFID as CoreRFID
from apps.groups.models import SecurityGroup
from apps.media.models import MediaBucket, MediaFile, media_bucket_slug, media_file_path
from apps.links.reference_utils import url_targets_local_loopback

from .. import store

__all__ = [
    "apps",
    "binascii",
    "hashlib",
    "json",
    "os",
    "re",
    "secrets",
    "socket",
    "uuid",
    "datetime",
    "timedelta",
    "Decimal",
    "InvalidOperation",
    "Iterable",
    "Sequence",
    "settings",
    "Site",
    "ValidationError",
    "models",
    "DecimalField",
    "OuterRef",
    "Prefetch",
    "Q",
    "Subquery",
    "reverse",
    "timezone",
    "_",
    "async_to_sync",
    "Entity",
    "EntityManager",
    "Node",
    "CustomerAccount",
    "EnergyTariff",
    "Location",
    "Reference",
    "CoreRFID",
    "SecurityGroup",
    "MediaBucket",
    "MediaFile",
    "media_bucket_slug",
    "media_file_path",
    "url_targets_local_loopback",
    "store",
]
