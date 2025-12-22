from django.db import migrations


OLD_TASK_NAMES = [
    "pages_purge_landing_leads",
    "nodes_poll_upstream_messages",
    "nodes_purge_net_messages",
    "nodes_update_all_information",
    "nodes_monitor_network_connectivity",
    "poll_email_collectors",
]

OLD_TASK_PATHS = [
    "apps.sites.tasks.purge_expired_landing_leads",
    "apps.nodes.tasks.poll_unreachable_upstream",
    "apps.nodes.tasks.purge_stale_net_messages",
    "apps.nodes.tasks.update_peer_nodes_information",
    "apps.nodes.tasks.monitor_network_connectivity",
    "apps.core.tasks.poll_email_collectors",
]


def remove_old_periodic_tasks(apps, schema_editor):
    del schema_editor

    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__in=OLD_TASK_NAMES).delete()
    PeriodicTask.objects.filter(task__in=OLD_TASK_PATHS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("celery", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(remove_old_periodic_tasks, migrations.RunPython.noop),
    ]
