from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.ocpp.admin.charge_point.admin import ChargerAdmin
from apps.ocpp.models import Charger, ControlOperationEvent, SecurityEvent, Transaction

class OperatorAdminSurfacesTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = ChargerAdmin(Charger, self.site)
        self.superuser = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.superuser)
        self.charger = Charger.objects.create(
            charger_id="CP-OPS-1",
            availability_state="Operative",
            last_online_at=timezone.now(),
        )

