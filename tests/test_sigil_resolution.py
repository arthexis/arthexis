import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase
from django.contrib.contenttypes.models import ContentType

from core.models import SigilRoot, OdooProfile, EmailInbox, InviteLead
from core.sigil_context import set_context, clear_context


class SigilResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create(username="sigiluser")

    def test_env_variable_sigil(self):
        os.environ["SIGIL_PATH"] = "demo"
        profile = OdooProfile.objects.create(
            user=self.user,
            host="path=[ENV.SIGIL_PATH]",
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
            host="lang=[SYS.LANGUAGE_CODE]",
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
            host=f"[CMD.SEND_INVITE={email}]",
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
        root = SigilRoot.objects.filter(prefix="OP").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="OP", context_type=SigilRoot.Context.ENTITY, content_type=ct
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

    def test_entity_sigil_with_id(self):
        ct = ContentType.objects.get_for_model(OdooProfile)
        root = SigilRoot.objects.filter(prefix="OP").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="OP", context_type=SigilRoot.Context.ENTITY, content_type=ct
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
        root = SigilRoot.objects.filter(prefix="OP").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="OP", context_type=SigilRoot.Context.ENTITY, content_type=ct
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
        root = SigilRoot.objects.filter(prefix="OP").first()
        if not root:
            root = SigilRoot.objects.create(
                prefix="OP", context_type=SigilRoot.Context.ENTITY, content_type=ct
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
