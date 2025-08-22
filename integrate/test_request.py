import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import TestCase
from django.contrib.auth import get_user_model

from .models import RequestType, Request


class RequestModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.requester = User.objects.create_user(username="req")
        self.approver = User.objects.create_user(username="app")
        self.req_type = RequestType.objects.create(code="ABC", name="Test")

    def test_number_auto_increment(self):
        r1 = Request.objects.create(
            request_type=self.req_type,
            description="first",
            requester=self.requester,
            approver=self.approver,
        )
        r2 = Request.objects.create(
            request_type=self.req_type,
            description="second",
            requester=self.requester,
            approver=self.approver,
        )
        self.assertEqual(r1.number, "ABC30000")
        self.assertEqual(r2.number, "ABC30001")

    def test_approve_makes_read_only(self):
        r = Request.objects.create(
            request_type=self.req_type,
            description="needs approval",
            requester=self.requester,
            approver=self.approver,
        )
        r.approve("ok")
        self.assertEqual(r.status, Request.Status.APPROVED)
        self.assertIsNotNone(r.responded_at)
        with self.assertRaises(ValueError):
            r.description = "changed"
            r.save()
