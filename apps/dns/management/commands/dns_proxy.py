from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.dns.models import DNSProxyConfig
from apps.dns.proxy import DNSProxyRuntimeConfig, DNSProxyServer, parse_dns_servers


class Command(BaseCommand):
    help = "Run the local DNS proxy server."

    def add_arguments(self, parser):
        parser.add_argument(
            "--config-id",
            type=int,
            help="DNSProxyConfig ID to load from the database.",
        )
        parser.add_argument(
            "--listen-host",
            type=str,
            default="127.0.0.1",
            help="Listen host for the DNS proxy.",
        )
        parser.add_argument(
            "--listen-port",
            type=int,
            default=5353,
            help="Listen port for the DNS proxy.",
        )
        parser.add_argument(
            "--upstream",
            action="append",
            default=[],
            help="Upstream DNS server IP (repeatable).",
        )
        parser.add_argument(
            "--use-tcp-upstream",
            action="store_true",
            help="Send upstream DNS queries over TCP.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=2.0,
            help="Timeout in seconds for upstream queries.",
        )

    def handle(self, *args, **options):
        config_id = options.get("config_id")
        if config_id:
            config = DNSProxyConfig.objects.filter(pk=config_id, is_enabled=True).first()
            if not config:
                raise CommandError("DNS proxy configuration not found or disabled.")
            runtime = config.to_runtime_config()
        else:
            upstreams: list[str] = []
            for raw in options.get("upstream", []):
                upstreams.extend(parse_dns_servers(raw))
            if not upstreams:
                raise CommandError("At least one upstream server is required.")
            runtime = DNSProxyRuntimeConfig(
                listen_host=options["listen_host"],
                listen_port=options["listen_port"],
                upstream_servers=upstreams,
                use_tcp=options["use_tcp_upstream"],
                timeout_seconds=options["timeout"],
            )

        server = DNSProxyServer(runtime)
        self.stdout.write(
            self.style.SUCCESS(
                "Starting DNS proxy on "
                f"{runtime.listen_host}:{runtime.listen_port} -> {runtime.upstream_servers}"
            )
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:  # pragma: no cover - manual stop
            self.stdout.write("Stopping DNS proxy...")
            server.shutdown()
