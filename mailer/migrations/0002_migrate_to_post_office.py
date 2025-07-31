from django.db import migrations


def migrate_queued_emails(apps, schema_editor):
    QueuedEmail = apps.get_model('mailer', 'QueuedEmail')
    from post_office import mail
    for qe in QueuedEmail.objects.filter(sent=False).select_related('template'):
        tpl = qe.template
        subject = tpl.subject.format(**qe.context)
        body = tpl.body.format(**qe.context)
        mail.send(recipients=[qe.to], subject=subject, message=body)


class Migration(migrations.Migration):
    dependencies = [
        ('mailer', '0001_initial'),
        ('post_office', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(migrate_queued_emails, migrations.RunPython.noop),
        migrations.DeleteModel('QueuedEmail'),
    ]
