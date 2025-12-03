import django.contrib.auth.models
import django.contrib.auth.validators
import django.core.validators
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import apps.base.models
import apps.sigils.fields


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="User",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("password", models.CharField(max_length=128, verbose_name="password")),
                        (
                            "last_login",
                            models.DateTimeField(
                                blank=True, null=True, verbose_name="last login"
                            ),
                        ),
                        (
                            "is_superuser",
                            models.BooleanField(
                                default=False,
                                help_text="Designates that this user has all permissions without explicitly assigning them.",
                                verbose_name="superuser status",
                            ),
                        ),
                        (
                            "username",
                            models.CharField(
                                error_messages={
                                    "unique": "A user with that username already exists."
                                },
                                help_text="Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.",
                                max_length=150,
                                unique=True,
                                validators=[
                                    django.contrib.auth.validators.UnicodeUsernameValidator()
                                ],
                                verbose_name="username",
                            ),
                        ),
                        (
                            "first_name",
                            models.CharField(
                                blank=True, max_length=150, verbose_name="first name"
                            ),
                        ),
                        (
                            "last_name",
                            models.CharField(
                                blank=True, max_length=150, verbose_name="last name"
                            ),
                        ),
                        (
                            "email",
                            models.EmailField(
                                blank=True, max_length=254, verbose_name="email address"
                            ),
                        ),
                        (
                            "is_staff",
                            models.BooleanField(
                                default=False,
                                help_text="Designates whether the user can log into this admin site.",
                                verbose_name="staff status",
                            ),
                        ),
                        (
                            "date_joined",
                            models.DateTimeField(
                                default=django.utils.timezone.now, verbose_name="date joined"
                            ),
                        ),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        ("data_path", models.CharField(blank=True, max_length=255)),
                        (
                            "last_visit_ip_address",
                            models.CharField(
                                blank=True,
                                max_length=45,
                                validators=[django.core.validators.validate_ipv46_address],
                            ),
                        ),
                        (
                            "is_active",
                            models.BooleanField(
                                default=True,
                                help_text="Designates whether this user should be treated as active. Unselect this instead of deleting customer accounts.",
                                verbose_name="active",
                            ),
                        ),
                        (
                            "require_2fa",
                            models.BooleanField(
                                default=False,
                                help_text="Require both a password and authenticator code to sign in.",
                                verbose_name="require 2FA",
                            ),
                        ),
                        (
                            "temporary_expires_at",
                            models.DateTimeField(
                                blank=True,
                                help_text="Automatically deactivate this account after the selected date and time.",
                                null=True,
                            ),
                        ),
                        (
                            "groups",
                            models.ManyToManyField(
                                blank=True,
                                help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.",
                                related_name="user_set",
                                related_query_name="user",
                                to="auth.group",
                                verbose_name="groups",
                            ),
                        ),
                        (
                            "operate_as",
                            models.ForeignKey(
                                blank=True,
                                help_text="Operate using another user's permissions when additional authority is required.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="operated_users",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        (
                            "site_template",
                            models.ForeignKey(
                                blank=True,
                                help_text="Branding template to apply for this user when overriding the site default.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="users",
                                to="pages.sitetemplate",
                                verbose_name="Site template",
                            ),
                        ),
                        (
                            "user_permissions",
                            models.ManyToManyField(
                                blank=True,
                                help_text="Specific permissions for this user.",
                                related_name="user_set",
                                related_query_name="user",
                                to="auth.permission",
                                verbose_name="user permissions",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "User",
                        "verbose_name_plural": "Users",
                        "abstract": False,
                        "db_table": "core_user",
                    },
                    managers=[
                        ("objects", apps.base.models.EntityUserManager()),
                        ("all_objects", django.contrib.auth.models.UserManager()),
                    ],
                ),
                migrations.CreateModel(
                    name="UserPhoneNumber",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        ("priority", models.PositiveIntegerField(default=0)),
                        (
                            "number",
                            models.CharField(
                                help_text="Digits only; punctuation is stripped automatically.",
                                max_length=32,
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="phone_numbers",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "ordering": ["priority", "pk"],
                        "verbose_name": "Phone Number",
                        "verbose_name_plural": "Phone Numbers",
                        "db_table": "core_userphonenumber",
                    },
                ),
                migrations.CreateModel(
                    name="PasskeyCredential",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        (
                            "name",
                            models.CharField(
                                help_text="Friendly label shown on the security settings page.",
                                max_length=80,
                            ),
                        ),
                        (
                            "credential_id",
                            models.CharField(
                                help_text="Base64-encoded identifier returned by the authenticator.",
                                max_length=255,
                                unique=True,
                            ),
                        ),
                        ("public_key", models.BinaryField()),
                        ("sign_count", models.PositiveIntegerField(default=0)),
                        ("user_handle", models.CharField(max_length=255)),
                        ("transports", models.JSONField(blank=True, default=list)),
                        ("last_used_at", models.DateTimeField(blank=True, null=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="passkeys",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "ordering": ("name", "created_at"),
                        "verbose_name": "Passkey",
                        "verbose_name_plural": "Passkeys",
                        "db_table": "core_passkeycredential",
                    },
                ),
                migrations.CreateModel(
                    name="GoogleCalendarProfile",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        (
                            "calendar_id",
                            apps.sigils.fields.SigilShortAutoField(
                                max_length=255, verbose_name="Calendar ID"
                            ),
                        ),
                        (
                            "api_key",
                            apps.sigils.fields.SigilShortAutoField(
                                max_length=255, verbose_name="API Key"
                            ),
                        ),
                        (
                            "display_name",
                            models.CharField(
                                blank=True, max_length=255, verbose_name="Display Name"
                            ),
                        ),
                        (
                            "max_events",
                            models.PositiveIntegerField(
                                default=5,
                                help_text="Limit the number of upcoming events displayed.",
                                verbose_name="Max Events",
                            ),
                        ),
                        (
                            "event_age_days",
                            models.PositiveIntegerField(
                                default=30,
                                help_text="Ignore events that started more than the specified days ago.",
                                verbose_name="Event Age (Days)",
                            ),
                        ),
                        (
                            "colors",
                            models.JSONField(
                                default=dict,
                                help_text="Mapping of calendar colors keyed by Calendar ID.",
                                verbose_name="Colors",
                            ),
                        ),
                        (
                            "group",
                            models.OneToOneField(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to="core.securitygroup",
                            ),
                        ),
                        (
                            "user",
                            models.OneToOneField(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Google Calendar Profile",
                        "verbose_name_plural": "Google Calendar Profiles",
                        "db_table": "core_googlecalendarprofile",
                    },
                ),
                migrations.AddConstraint(
                    model_name="passkeycredential",
                    constraint=models.UniqueConstraint(
                        fields=("user", "name"),
                        name="core_passkey_unique_name_per_user",
                    ),
                ),
            ],
            database_operations=[],
        )
    ]
