from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0001_initial'),
        ('accounts', '0011_credit_created_by'),
    ]

    operations = [
        migrations.RenameField(
            model_name='subscription',
            old_name='user',
            new_name='account',
        ),
        migrations.AlterField(
            model_name='subscription',
            name='account',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='accounts.account'),
        ),
    ]
