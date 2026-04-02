from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.test import TestCase
from django.utils import timezone

from apps.credentials.admin import SSHAccountAdmin
from apps.credentials.models import SSHAccount
from apps.nodes.models import Node

class SSHAccountAdminTests(TestCase):
    def setUp(self):
        self.admin = SSHAccountAdmin(SSHAccount, AdminSite())
        self.node = Node.objects.create(hostname="ops-node-1")

    def test_credential_status_missing_when_no_auth_material(self):
        account = SSHAccount.objects.create(node=self.node, username="ops")

        self.assertEqual(self.admin.credential_status(account), "Missing")

