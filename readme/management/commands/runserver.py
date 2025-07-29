from daphne.management.commands.runserver import Command as RunserverCommand

class Command(RunserverCommand):
    """Extended runserver command that also prints WebSocket URLs."""

    def on_bind(self, server_port):
        super().on_bind(server_port)
        host = self.addr or (self.default_addr_ipv6 if self.use_ipv6 else self.default_addr)
        scheme = 'wss' if self.ssl_options else 'ws'
        # Display available websocket URLs.
        websocket_paths = ['/ws/echo/', '/<path>/<cid>/']
        for path in websocket_paths:
            self.stdout.write(f"WebSocket available at {scheme}://{host}:{server_port}{path}")



