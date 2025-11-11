import os
from unittest import mock
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.contrib.contenttypes.models import ContentType

from core.models import SigilRoot, OdooProfile, EmailArtifact
from teams.models import EmailInbox, EmailCollector
from nodes.models import NodeRole
from core.sigil_builder import (
    generate_model_sigils,
    _resolve_sigil,
    resolve_sigils_in_text,
)
from core.sigil_context import set_context, clear_context
from core import sigil_resolver, system


class SigilResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create(
            username="sigiluser", email="sigil@example.com"
        )
        inbox_owner = get_user_model().objects.create(
            username="sigilinbox", email="inbox@example.com"
        )
        inbox = EmailInbox.objects.create(
            user=inbox_owner,
            username="u",
            host="h",
            password="p",
        )
        collector = EmailCollector.objects.create(inbox=inbox)
        EmailArtifact.objects.create(
            collector=collector,
            subject="first",
            sender="a@test",
            body="",
            sigils={},
            fingerprint="f1",
        )
        EmailArtifact.objects.create(
            collector=collector,
            subject="second",
            sender=cls.user.email,
            body="",
            sigils={},
            fingerprint="f2",
        )
        ct = ContentType.objects.get_for_model(EmailArtifact)
        SigilRoot.objects.update_or_create(
            prefix="EART",
            defaults={
                "context_type": SigilRoot.Context.ENTITY,
                "content_type": ct,
            },
        )
        ct_user = ContentType.objects.get_for_model(get_user_model())
        SigilRoot.objects.update_or_create(
            prefix="USER",
            defaults={
                "context_type": SigilRoot.Context.ENTITY,
                "content_type": ct_user,
            },
        )
        SigilRoot.objects.update_or_create(
            prefix="CONF",
            defaults={
                "context_type": SigilRoot.Context.CONFIG,
                "content_type": None,
            },
        )
        SigilRoot.objects.update_or_create(
            prefix="SYS",
            defaults={
                "context_type": SigilRoot.Context.CONFIG,
                "content_type": None,
            },
        )

    def test_unknown_root_sigil_left_intact(self):
        profile = OdooProfile.objects.create(
            user=self.user,
            host="url=[FOO.BAR]",
            database="db",
            username="odoo",
            password="secret",
        )
        tmpl = Template("{{ profile.host }}")
        with self.assertLogs("core.entity", level="WARNING") as cm:
            rendered = tmpl.render(Context({"profile": profile}))
        self.assertEqual(rendered, "url=[FOO.BAR]")
        self.assertIn("Unknown sigil root [FOO]", cm.output[0])

    def test_cmd_sigil_root_removed(self):
        SigilRoot.objects.filter(prefix__iexact="CMD").delete()
        generate_model_sigils()
        self.assertFalse(SigilRoot.objects.filter(prefix__iexact="CMD").exists())
        self.assertEqual(_resolve_sigil("[CMD.showmigrations]"), "[CMD.showmigrations]")

    def test_entity_sigil(self):
        ct = ContentType.objects.get_for_model(OdooProfile)
        root = SigilRoot.objects.filter(prefix="ODOO").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="ODOO", context_type=SigilRoot.Context.ENTITY, content_type=ct
            )
        profile = OdooProfile.objects.create(
            user=self.user,
            host=f"user=[{root.prefix}.USERNAME]",
            database="db",
            username="odoo",
            password="secret",
        )
        tmpl = Template("{{ profile.host }}")
        rendered = tmpl.render(Context({"profile": profile}))
        self.assertEqual(rendered, "user=odoo")

    @override_settings(LEGACY_SIGIL_VALUE="legacy")
    def test_conf_sigil_resolution(self):
        SigilRoot.objects.update_or_create(
            prefix="CONF",
            defaults={
                "context_type": SigilRoot.Context.CONFIG,
                "content_type": None,
            },
        )
        resolved = sigil_resolver._resolve_token("CONF.LEGACY_SIGIL_VALUE")
        self.assertEqual(resolved, "legacy")

    def test_sys_sigil_resolution(self):
        with mock.patch(
            "core.sigil_resolver.get_system_sigil_values",
            return_value={"REVISION": "123456"},
        ):
            resolved = sigil_resolver._resolve_token("SYS.REVISION")
        self.assertEqual(resolved, "123456")

    def test_sys_sigil_case_insensitive(self):
        with mock.patch(
            "core.sigil_resolver.get_system_sigil_values",
            return_value={"RUNNING": "True"},
        ):
            resolved = resolve_sigils_in_text("[sys.running]")
        self.assertEqual(resolved, "True")

    def test_sys_namespace_resolution(self):
        with mock.patch(
            "core.sigil_resolver.get_system_sigil_values",
            return_value={},
        ):
            with mock.patch(
                "core.sigil_resolver.resolve_system_namespace_value",
                return_value="soon",
            ):
                resolved = sigil_resolver._resolve_token(
                    "SYS.NEXT-VER-CHECK"
                )
        self.assertEqual(resolved, "soon")

    def test_system_namespace_value_normalizes_hyphen(self):
        with mock.patch("core.system._auto_upgrade_next_check", return_value="later"):
            result = system.resolve_system_namespace_value("next-ver-check")
        self.assertEqual(result, "later")

    def test_system_namespace_value_supports_legacy_auto_upgrade(self):
        with mock.patch("core.system._auto_upgrade_next_check", return_value="legacy"):
            result = system.resolve_system_namespace_value(
                "auto-upgrade.next-check"
            )
        self.assertEqual(result, "legacy")

    def test_entity_sigil_hyphen_field(self):
        ct = ContentType.objects.get_for_model(OdooProfile)
        root = SigilRoot.objects.filter(prefix="ODOO").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="ODOO", context_type=SigilRoot.Context.ENTITY, content_type=ct
            )
        profile = OdooProfile.objects.create(
            user=self.user,
            host=f"uid=[{root.prefix}.ODOO-UID]",
            database="db",
            username="odoo",
            password="secret",
            odoo_uid=42,
        )
        tmpl = Template("{{ profile.host }}")
        rendered = tmpl.render(Context({"profile": profile}))
        self.assertEqual(rendered, "uid=42")

    def test_entity_sigil_with_id(self):
        ct = ContentType.objects.get_for_model(OdooProfile)
        root = SigilRoot.objects.filter(prefix="ODOO").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="ODOO", context_type=SigilRoot.Context.ENTITY, content_type=ct
            )
        src_user = get_user_model().objects.create(username="srcuser")
        src = OdooProfile.objects.create(
            user=src_user,
            host="h",
            database="db",
            username="srcuser",
            password="secret",
        )
        profile = OdooProfile.objects.create(
            user=self.user,
            host=f"user=[{root.prefix}={src.pk}.USERNAME]",
            database="db",
            username="odoo",
            password="secret",
        )
        tmpl = Template("{{ profile.host }}")
        rendered = tmpl.render(Context({"profile": profile}))
        self.assertEqual(rendered, "user=srcuser")

    def test_entity_sigil_field_filter(self):
        ct = ContentType.objects.get_for_model(OdooProfile)
        root = SigilRoot.objects.filter(prefix="ODOO").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="ODOO", context_type=SigilRoot.Context.ENTITY, content_type=ct
            )
        src_user = get_user_model().objects.create(username="fieldsrc")
        src = OdooProfile.objects.create(
            user=src_user,
            host="h",
            database="db_src",
            username="odoo",
            password="secret",
        )
        profile = OdooProfile.objects.create(
            user=self.user,
            host=f"db=[{root.prefix}:USER={src_user.pk}.DATABASE]",
            database="db",
            username="odoo",
            password="secret",
        )
        tmpl = Template("{{ profile.host }}")
        rendered = tmpl.render(Context({"profile": profile}))
        self.assertEqual(rendered, "db=db_src")

    def test_entity_sigil_from_context(self):
        ct = ContentType.objects.get_for_model(OdooProfile)
        root = SigilRoot.objects.filter(prefix="ODOO").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="ODOO", context_type=SigilRoot.Context.ENTITY, content_type=ct
            )
        src_user = get_user_model().objects.create(username="ctxuser_src")
        src = OdooProfile.objects.create(
            user=src_user,
            host="h",
            database="db",
            username="ctxuser",
            password="secret",
        )
        set_context({OdooProfile: src.pk})
        try:
            inbox = EmailInbox.objects.create(
                user=self.user,
                username=f"user=[{root.prefix}.USERNAME]",
                host="host",
                port=993,
                password="pwd",
                protocol=EmailInbox.IMAP,
            )
            tmpl = Template("{{ inbox.username }}")
            rendered = tmpl.render(Context({"inbox": inbox}))
            self.assertEqual(rendered, "user=ctxuser")
        finally:
            clear_context()

    def test_entity_sigil_random_instance(self):
        ct = ContentType.objects.get_for_model(OdooProfile)
        root = SigilRoot.objects.filter(prefix="ODOO").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="ODOO", context_type=SigilRoot.Context.ENTITY, content_type=ct
            )
        u1 = get_user_model().objects.create(username="randuser1")
        p1 = OdooProfile.objects.create(
            user=u1,
            host="h1",
            database="db",
            username="rand1",
            password="secret",
        )
        u2 = get_user_model().objects.create(username="randuser2")
        p2 = OdooProfile.objects.create(
            user=u2,
            host="h2",
            database="db",
            username="rand2",
            password="secret",
        )
        inbox = EmailInbox.objects.create(
            user=self.user,
            username=f"user=[{root.prefix}.USERNAME]",
            host="host",
            port=993,
            password="pwd",
            protocol=EmailInbox.IMAP,
        )
        tmpl = Template("{{ inbox.username }}")
        rendered = tmpl.render(Context({"inbox": inbox}))
        names = set(OdooProfile.objects.values_list("username", flat=True))
        self.assertIn(rendered, {f"user={n}" for n in names})

    def test_node_role_name_sigil(self):
        ct = ContentType.objects.get_for_model(NodeRole)
        root = SigilRoot.objects.filter(prefix="ROLE").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="ROLE", context_type=SigilRoot.Context.ENTITY, content_type=ct
            )
        term = NodeRole.objects.create(name="Terminal", description="term role")
        other = NodeRole.objects.create(
            name="Other", description="[ROLE=Terminal.DESCRIPTION]"
        )
        self.assertEqual(other.resolve_sigils("description"), term.description)
        self.assertEqual(
            _resolve_sigil("[ROLE=Terminal.DESCRIPTION]"), term.description
        )

    def test_node_role_serialized_sigil(self):
        ct = ContentType.objects.get_for_model(NodeRole)
        root = SigilRoot.objects.filter(prefix="ROLE").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="ROLE", context_type=SigilRoot.Context.ENTITY, content_type=ct
            )
        term = NodeRole.objects.create(name="Terminal", description="term role")
        other = NodeRole.objects.create(
            name="Other", description=f"[{root.prefix}=Terminal]"
        )
        expected = [
            {
                "model": "nodes.noderole",
                "pk": term.pk,
                "fields": {
                    "is_seed_data": False,
                    "is_deleted": False,
                    "is_user_data": False,
                    "name": "Terminal",
                    "description": "term role",
                },
            }
        ]
        self.assertJSONEqual(other.resolve_sigils("description"), expected)
        self.assertJSONEqual(_resolve_sigil(f"[{root.prefix}=Terminal]"), expected)

    def test_user_sigil_defaults_to_current_user(self):
        set_context({get_user_model(): self.user.pk})
        try:
            result = resolve_sigils_in_text("[USER.EMAIL]")
            self.assertEqual(result, self.user.email)
        finally:
            clear_context()

    def test_email_sigil_ordering_and_nested(self):
        set_context({get_user_model(): self.user.pk})
        try:
            self.assertEqual(resolve_sigils_in_text("[EART.SUBJECT]"), "second")
            nested = resolve_sigils_in_text("[EART.SENDER=[USER.EMAIL]]")
            self.assertEqual(nested, self.user.email)
        finally:
            clear_context()

    def test_env_sigil(self):
        os.environ["SIGIL_TEST_VAR"] = "env-val"
        try:
            self.assertEqual(
                resolve_sigils_in_text("[ENV.SIGIL_TEST_VAR]"),
                "env-val",
            )
        finally:
            del os.environ["SIGIL_TEST_VAR"]

    def test_env_sigil_case_insensitive(self):
        os.environ["sigil_test_var"] = "env-lower"
        try:
            self.assertEqual(
                resolve_sigils_in_text("[env.sigil_test_var]"),
                "env-lower",
            )
            self.assertEqual(
                resolve_sigils_in_text("[ENV.sigil_test_var]"),
                "env-lower",
            )
            self.assertEqual(
                resolve_sigils_in_text("[Env.SIGIL_TEST_VAR]"),
                "env-lower",
            )
        finally:
            del os.environ["sigil_test_var"]

    def test_conf_sigil(self):
        with self.settings(SIGIL_TEST_SETTING="sys-val"):
            self.assertEqual(
                resolve_sigils_in_text("[CONF.SIGIL_TEST_SETTING]"),
                "sys-val",
            )

    def test_conf_sigil_case_insensitive(self):
        with self.settings(sigil_case_setting="sys-lower"):
            self.assertEqual(
                resolve_sigils_in_text("[CONF.sigil_case_setting]"),
                "sys-lower",
            )

    def test_unknown_sigil_without_gway_returns_literal(self):
        result = resolve_sigils_in_text("[unknown.sigil]")
        self.assertEqual(result, "[unknown.sigil]")
