import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase
from django.contrib.contenttypes.models import ContentType

from core.models import (
    SigilRoot,
    OdooProfile,
    EmailInbox,
    InviteLead,
    EmailCollector,
    EmailArtifact,
)
from nodes.models import NodeRole
from core.sigil_builder import _resolve_sigil, resolve_sigils_in_text
from core.sigil_context import set_context, clear_context


class SigilResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create(
            username="sigiluser", email="sigil@example.com"
        )
        inbox = EmailInbox.objects.create(
            user=cls.user,
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
            prefix="EMAIL",
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

    def test_env_variable_sigil(self):
        os.environ["SIGIL_PATH"] = "demo"
        profile = OdooProfile.objects.create(
            user=self.user,
            host="path=[ENV.SIGIL-PATH]",
            database="db",
            username="odoo",
            password="secret",
        )
        tmpl = Template("{{ profile.host }}")
        rendered = tmpl.render(Context({"profile": profile}))
        self.assertEqual(rendered, "path=demo")

    def test_settings_sigil(self):
        profile = OdooProfile.objects.create(
            user=self.user,
            host="lang=[SYS.LANGUAGE-CODE]",
            database="db",
            username="odoo",
            password="secret",
        )
        tmpl = Template("{{ profile.host }}")
        rendered = tmpl.render(Context({"profile": profile}))
        expected = f"lang={settings.LANGUAGE_CODE}"
        self.assertEqual(rendered, expected)

    def test_command_sigil(self):
        email = "invite@example.com"
        User = get_user_model()
        User.objects.create_user(username="cmduser", email=email)
        InviteLead.objects.create(email=email)
        profile = OdooProfile.objects.create(
            user=self.user,
            host=f"[CMD.SEND-INVITE={email}]",
            database="db",
            username="odoo",
            password="secret",
        )
        tmpl = Template("{{ profile.host }}")
        rendered = tmpl.render(Context({"profile": profile}))
        self.assertIn("Invitation sent", rendered)

    def test_unresolved_env_sigil_left_intact(self):
        profile = OdooProfile.objects.create(
            user=self.user,
            host="path=[ENV.MISSING_PATH]",
            database="db",
            username="odoo",
            password="secret",
        )
        tmpl = Template("{{ profile.host }}")
        with self.assertLogs("core.entity", level="WARNING") as cm:
            rendered = tmpl.render(Context({"profile": profile}))
        self.assertEqual(rendered, "path=[ENV.MISSING_PATH]")
        self.assertIn(
            "Missing environment variable for sigil [ENV.MISSING_PATH]", cm.output[0]
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
            self.assertEqual(resolve_sigils_in_text("[EMAIL.SUBJECT]"), "second")
            nested = resolve_sigils_in_text("[EMAIL.SENDER=[USER.EMAIL]]")
            self.assertEqual(nested, self.user.email)
        finally:
            clear_context()
