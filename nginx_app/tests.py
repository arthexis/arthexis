from django.test import TestCase
from .models import NginxConfig
import threading
import http.server
import socketserver


class NginxConfigTests(TestCase):
    def _run_server(self, port):
        handler = http.server.SimpleHTTPRequestHandler
        httpd = socketserver.TCPServer(("", port), handler)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.daemon = True
        thread.start()
        return httpd

    def test_render_config_contains_backup(self):
        cfg = NginxConfig(name='test', server_name='example.com', primary_upstream='remote:8000', backup_upstream='127.0.0.1:8000')
        text = cfg.render_config()
        self.assertIn('backup', text)
        self.assertIn('proxy_set_header Upgrade $http_upgrade;', text)

    def test_connection(self):
        server = self._run_server(8123)
        try:
            cfg = NginxConfig(name='test', server_name='example.com', primary_upstream='127.0.0.1:8123')
            self.assertTrue(cfg.test_connection())
            cfg.primary_upstream = '127.0.0.1:8999'
            self.assertFalse(cfg.test_connection())
        finally:
            server.shutdown()
            server.server_close()
