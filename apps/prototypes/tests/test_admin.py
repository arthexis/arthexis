from __future__ import annotations

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.prototypes.admin import PrototypeAdmin
from apps.prototypes.models import Prototype


def test_prototype_admin_exposes_no_mutating_actions():
    request = RequestFactory().get("/admin/prototypes/prototype/")
    request.user = AnonymousUser()
    admin = PrototypeAdmin(Prototype, AdminSite())

    assert admin.get_actions(request) == {}
