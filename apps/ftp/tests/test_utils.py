from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.groups.models import SecurityGroup

from ..models import FTPFolder
from ..utils import build_user_mounts


class BuildUserMountsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="owner", password="secret123"
        )
        self.member = get_user_model().objects.create_user(
            username="member", password="secret123"
        )
        self.group = SecurityGroup.objects.create(name="Operators")
        self.member.groups.add(self.group)

    def _assert_is_link(self, link_path: Path) -> None:
        if os.name == "nt":
            self.assertTrue(link_path.exists())
            self.assertTrue(link_path.is_dir())
        else:
            self.assertTrue(link_path.is_symlink())

    def test_builds_mounts_with_permissions(self):
        with TemporaryDirectory() as tmpdir:
            folder_path = Path(tmpdir) / "data"
            folder_path.mkdir()

            folder = FTPFolder.objects.create(
                name="Data",
                path=str(folder_path),
                enabled=True,
                user=self.owner,
                group=self.group,
                owner_permission=FTPFolder.Permission.FULL_CONTROL,
                group_permission=FTPFolder.Permission.READ_ONLY,
            )

            mount_root = Path(tmpdir) / "mounts"
            mounts, warnings = build_user_mounts(FTPFolder.objects.all(), mount_root)

            self.assertFalse(warnings)
            self.assertIn(self.owner.username, mounts)
            self.assertIn(self.member.username, mounts)

            owner_mount = mounts[self.owner.username]
            owner_link = owner_mount.home / folder.build_link_name()
            self.assertTrue(owner_mount.permissions.startswith("elr"))
            self._assert_is_link(owner_link)
            self.assertEqual(owner_link.resolve(), folder_path.resolve())

            member_mount = mounts[self.member.username]
            member_link = member_mount.home / folder.build_link_name()
            self._assert_is_link(member_link)
            self.assertEqual(member_link.resolve(), folder_path.resolve())
            self.assertNotIn("w", member_mount.permissions)

    def test_missing_target_generates_warning(self):
        with TemporaryDirectory() as tmpdir:
            FTPFolder.objects.create(
                name="Missing",
                path=str(Path(tmpdir) / "missing"),
                enabled=True,
                user=self.owner,
            )
            mounts, warnings = build_user_mounts(
                FTPFolder.objects.all(), Path(tmpdir) / "mounts"
            )
            self.assertFalse(mounts)
            self.assertEqual(len(warnings), 1)
            self.assertIn("does not exist", warnings[0])
