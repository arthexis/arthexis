"""Focused project admin security regression tests."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.projects.models import Project, ProjectItem

