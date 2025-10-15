import socket
import socketserver
import tempfile
import threading
from pathlib import Path

from django.test import TestCase

from core.models import TelnetProxy
from core.telnet_proxy import get_proxy_runner, stop_all_proxies


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.received.append(b"<connected>")
        while True:
            data = self.request.recv(4096)
            if not data:
                break
            self.server.received.append(data)
            self.request.sendall(data.upper())


class TelnetProxyTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        class _Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        cls._upstream = _Server(("127.0.0.1", 0), _EchoHandler)
        cls._upstream.received = []  # type: ignore[attr-defined]
        cls._thread = threading.Thread(target=cls._upstream.serve_forever, daemon=True)
        cls._thread.start()
        cls._upstream_port = cls._upstream.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls._upstream.shutdown()
        cls._upstream.server_close()
        super().tearDownClass()

    def tearDown(self):
        stop_all_proxies()
        super().tearDown()

    def setUp(self):
        super().setUp()
        self._upstream.received.clear()

    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def test_proxy_forwards_messages_and_reports_status(self):
        proxy = TelnetProxy.objects.create(
            endpoint_host="127.0.0.1",
            endpoint_port=self._free_port(),
            telnet_host="127.0.0.1",
            telnet_port=self._upstream_port,
        )

        self.assertFalse(proxy.is_running())
        proxy.start()
        self.assertTrue(proxy.is_running())

        runner = get_proxy_runner(proxy)
        self.assertIsNotNone(runner)
        client_address = (proxy.endpoint_host, runner.listening_port)

        with socket.create_connection(client_address, timeout=5) as client:
            client.sendall(b"ping\n")
            response = client.recv(4096)

        self.assertEqual(response, b"PING\n")
        self.assertTrue(any(chunk == b"ping\n" for chunk in self._upstream.received))

    def test_proxy_writes_optional_logfile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "telnet.log"
            proxy = TelnetProxy.objects.create(
                endpoint_host="127.0.0.1",
                endpoint_port=self._free_port(),
                telnet_host="127.0.0.1",
                telnet_port=self._upstream_port,
                logfile=str(log_path),
            )

            proxy.start()
            runner = get_proxy_runner(proxy)
            self.assertIsNotNone(runner)
            client_address = (proxy.endpoint_host, runner.listening_port)

            with socket.create_connection(client_address, timeout=5) as client:
                client.sendall(b"hello")
                client.recv(4096)

            proxy.stop()

            self.assertTrue(log_path.exists())
            contents = log_path.read_text()
            self.assertIn("client→server hello", contents)
