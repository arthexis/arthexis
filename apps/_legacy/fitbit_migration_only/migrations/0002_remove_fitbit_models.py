"""Drop legacy Fitbit tables before removing the Fitbit app itself."""

from django.db import migrations


class Migration(migrations.Migration):
    """Remove persisted Fitbit schema while the temporary app is still installed.

    Parameters:
        None.

    Returns:
        None.
    """

    dependencies = [
        ("fitbit", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="FitbitHealthSample",
        ),
        migrations.DeleteModel(
            name="FitbitNetMessageDelivery",
        ),
        migrations.DeleteModel(
            name="FitbitConnection",
        ),
    ]
