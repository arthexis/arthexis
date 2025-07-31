import os
import subprocess
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from nginx_app.models import NginxConfig


class Command(BaseCommand):
    help = "Apply an NGINX template by writing it to the NGINX directory and reloading NGINX"

    def add_arguments(self, parser):
        parser.add_argument('config_id', type=int, help='ID of the NginxConfig to apply')

    def handle(self, *args, **options):
        config_id = options['config_id']
        try:
            cfg = NginxConfig.objects.get(pk=config_id)
        except NginxConfig.DoesNotExist as exc:
            raise CommandError(f"Configuration {config_id} does not exist") from exc

        root = getattr(settings, 'NGINX_CONFIG_ROOT', '/etc/nginx/conf.d')
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, f"{cfg.name}.conf")
        with open(path, 'w') as f:
            f.write(cfg.render_config())

        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['nginx', '-s', 'reload'], check=True)
        self.stdout.write(self.style.SUCCESS(f"Applied template {cfg.name} to {path}"))
