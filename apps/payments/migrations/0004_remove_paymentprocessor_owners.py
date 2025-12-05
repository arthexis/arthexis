from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0003_openpayprocessor_avatar_paypalprocessor_avatar_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="openpayprocessor",
            name="openpayprocessor_requires_owner",
        ),
        migrations.RemoveConstraint(
            model_name="paypalprocessor",
            name="paypalprocessor_requires_owner",
        ),
        migrations.RemoveConstraint(
            model_name="stripeprocessor",
            name="stripeprocessor_requires_owner",
        ),
        migrations.RemoveField(
            model_name="openpayprocessor",
            name="avatar",
        ),
        migrations.RemoveField(
            model_name="openpayprocessor",
            name="group",
        ),
        migrations.RemoveField(
            model_name="openpayprocessor",
            name="user",
        ),
        migrations.RemoveField(
            model_name="paypalprocessor",
            name="avatar",
        ),
        migrations.RemoveField(
            model_name="paypalprocessor",
            name="group",
        ),
        migrations.RemoveField(
            model_name="paypalprocessor",
            name="user",
        ),
        migrations.RemoveField(
            model_name="stripeprocessor",
            name="avatar",
        ),
        migrations.RemoveField(
            model_name="stripeprocessor",
            name="group",
        ),
        migrations.RemoveField(
            model_name="stripeprocessor",
            name="user",
        ),
    ]
