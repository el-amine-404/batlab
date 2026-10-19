"""The egress proxy (egress.py): what it refuses, and that what it lets
through arrives whole. Its sites here are servers on the loopback, let
through by a resolver of the test's own: no network."""

import asyncio
import json
import socket
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videodrop import egress  # noqa: E402
from videodrop.ytdlp import params  # noqa: E402
from videodrop.options import DEFAULTS  # noqa: E402


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _ask(proxy_port: int, raw: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=10) as sock:
        sock.sendall(raw)
        chunks = []
        while data := sock.recv(65536):
            chunks.append(data)
        return b"".join(chunks)


class Addresses(unittest.TestCase):
    def test_only_the_internet_is_public(self):
        for address in ("10.0.0.1", "172.19.0.5", "192.168.1.10", "127.0.0.1", "100.64.0.1", "169.254.169.254",
                        "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fd00::1", "::ffff:192.168.1.1", "::ffff:127.0.0.1"):
            self.assertFalse(egress.public(address), address)
        for address in ("8.8.8.8", "142.250.180.14", "2606:4700:4700::1111", "::ffff:8.8.8.8"):
            self.assertTrue(egress.public(address), address)

    def test_a_name_pointing_home_is_refused(self):
        with self.assertRaises(egress.Refused):
            asyncio.run(egress.resolve("localhost", 443))
        with self.assertRaises(egress.Refused):
            asyncio.run(egress.resolve("[::1]", 443))
        with self.assertRaises(egress.Refused):
            asyncio.run(egress.resolve("192.168.1.1", 80))

    def test_ffmpeg_and_ytdlp_go_through_it(self):
        found = params(DEFAULTS, "%(title)s.%(ext)s", Path("/tmp/w"), proxy="http://127.0.0.1:9")
        self.assertEqual(found["proxy"], "http://127.0.0.1:9")
        whitelist = found["external_downloader_args"]["ffmpeg_i"]
        self.assertEqual(whitelist[0], "-protocol_whitelist")
        self.assertNotIn("file", whitelist[1].split(","))
        self.assertNotIn("proxy", params(DEFAULTS, "%(title)s.%(ext)s", Path("/tmp/w")))


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=self.site.serve_forever, daemon=True).start()
        self.site_port = self.site.server_address[1]

        async def resolver(host, port):
            if host == "site.test":
                return ["127.0.0.1"]
            return await egress.resolve(host, port)

        self.proxy = egress.Proxy(resolver)
        self.port = int(self.proxy.start().rsplit(":", 1)[1])

    def tearDown(self):
        self.proxy.stop()
        self.site.shutdown()
        self.site.server_close()

    def test_refuses_the_home_network_by_address(self):
        for raw in (b"CONNECT 127.0.0.1:%d HTTP/1.1\r\nHost: x\r\n\r\n" % self.site_port,
                    b"CONNECT localhost:443 HTTP/1.1\r\n\r\n",
                    b"GET http://192.168.1.1/admin HTTP/1.1\r\nHost: 192.168.1.1\r\n\r\n",
                    b"GET http://[::1]:%d/ HTTP/1.1\r\n\r\n" % self.site_port):
            answer = _ask(self.port, raw)
            self.assertTrue(answer.startswith(b"HTTP/1.1 403 "), answer[:200])
            self.assertIn(b"an address on this network", answer)
        self.assertEqual(self.proxy.refused, 4)

    def test_refuses_what_isnt_web(self):
        for raw in (b"GET file:///etc/passwd HTTP/1.1\r\n\r\n", b"GET ftp://site.test/x HTTP/1.1\r\n\r\n",
                    b"GET / HTTP/1.1\r\nHost: site.test\r\n\r\n", b"nonsense\r\n\r\n"):
            self.assertTrue(_ask(self.port, raw).startswith(b"HTTP/1.1 400 "), raw)

    def test_a_tunnel_carries_the_bytes(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(b"CONNECT site.test:%d HTTP/1.1\r\nHost: site.test\r\n\r\n" % self.site_port)
            head = b""
            while b"\r\n\r\n" not in head:
                head += sock.recv(1)
            self.assertTrue(head.startswith(b"HTTP/1.1 200 "), head)
            sock.sendall(b"GET /in/the/tunnel HTTP/1.1\r\nHost: site.test\r\nConnection: close\r\n\r\n")
            answer = b""
            while data := sock.recv(65536):
                answer += data
        self.assertIn(b'"path": "/in/the/tunnel"', answer)

    def test_plain_http_is_passed_on_without_the_proxys_headers(self):
        answer = _ask(self.port, b"GET http://site.test:%d/a?b=1 HTTP/1.1\r\nHost: site.test\r\n"
                                 b"Proxy-Authorization: Basic eA==\r\nProxy-Connection: keep-alive\r\nX-Kept: yes\r\n"
                                 b"\r\n" % self.site_port)
        self.assertTrue(answer.startswith(b"HTTP/1.0 200") or answer.startswith(b"HTTP/1.1 200"), answer[:100])
        seen = json.loads(answer.split(b"\r\n\r\n", 1)[1])
        self.assertEqual(seen["path"], "/a?b=1")
        self.assertEqual(seen["headers"]["x-kept"], "yes")
        self.assertEqual(seen["headers"]["connection"], "close")
        self.assertNotIn("proxy-authorization", seen["headers"])
        self.assertNotIn("proxy-connection", seen["headers"])

    def test_a_locked_process_connects_only_through_it(self):
        script = (
            "import socket, sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
            "from videodrop import egress\n"
            f"egress.lock('http://127.0.0.1:{self.port}')\n"
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {self.site_port}), timeout=5)\n"
            "    print('reached the site')\n"
            "except ConnectionRefusedError as exc:\n"
            "    print('refused:', exc)\n"
            f"socket.create_connection(('127.0.0.1', {self.port}), timeout=5).close()\n"
            "print('proxy ok')\n"
        )
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("refused: videodrop: connections go through its proxy", done.stdout)
        self.assertIn("proxy ok", done.stdout)


if __name__ == "__main__":
    unittest.main()
