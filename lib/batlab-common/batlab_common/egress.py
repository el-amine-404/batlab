"""The only way out for what a link makes an app fetch (videodrop, songdrop):
an HTTP proxy on the loopback that connects to the internet and nowhere else.

A link's host is a name, and a name can point anywhere: at an address on this
network, or at one address when it is checked and another when it is fetched
(DNS rebinding). So the address is checked where the connection is made: the
proxy resolves the name itself, refuses it when any of its addresses isn't a
public one (a private range, the loopback, link-local, CGNAT, multicast…),
and connects to the very address it checked.

The processes that fetch links are handed its address: yt-dlp sends every
request through it (curl_cffi too), ffmpeg gets it and only web protocols
(FFMPEG_PROTOCOLS), and lock() refuses any other connection the process's
Python makes. HTTPS goes through as CONNECT tunnels (the proxy sees the host,
never the content); plain HTTP as one request per connection.
"""

import asyncio
import ipaddress
import logging
import socket
import threading
import time
import urllib.parse

log = logging.getLogger("egress")

HEAD_LIMIT = 32 * 1024
HEAD_TIMEOUT = 30
CONNECT_TIMEOUT = 15
CHUNK = 64 * 1024
MAX_OPEN = 512
# Request headers meant for the proxy, not for the site.
_HOP = {"proxy-connection", "proxy-authorization", "connection", "keep-alive", "te", "trailer", "upgrade"}


class Refused(Exception):
    """Said to the process that asked, as the reason of a 403."""


def public(address: str) -> bool:
    """Whether an address is on the internet: is_global leaves out the
    private ranges, the loopback, link-local, CGNAT (100.64/10), the
    documentation and benchmark ranges; multicast and IPv6's IPv4-mapped
    addresses are checked too."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    """The addresses to connect to for host; Refused when any of them isn't
    public (a name that points inside even once is refused)."""
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
        found = [host]
    except ValueError:
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), CONNECT_TIMEOUT)
        except (OSError, TimeoutError) as exc:
            raise ConnectionError(f"{host} doesn't resolve") from exc
        found = list(dict.fromkeys(info[4][0] for info in infos))
    inside = [address for address in found if not public(address)]
    if inside:
        raise Refused(f"{host} points to {inside[0]}, an address on this network, not the internet")
    if not found:
        raise ConnectionError(f"{host} doesn't resolve")
    return found


def _target(line: str) -> tuple[str, str, int, str | None]:
    """(method, host, port, origin-form path or None for CONNECT) of a request line."""
    try:
        method, target, version = line.split(" ")
    except ValueError:
        raise ValueError("not an HTTP request") from None
    if not version.startswith("HTTP/1."):
        raise ValueError("not HTTP/1")
    if method == "CONNECT":
        host, _, port = target.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError("CONNECT wants host:port")
        return method, host, int(port), None
    parts = urllib.parse.urlsplit(target)
    if parts.scheme != "http" or not parts.hostname or parts.username or parts.password:
        raise ValueError("only http:// requests (https goes by CONNECT)")
    path = parts.path or "/"
    return method, parts.hostname, parts.port or 80, path + (f"?{parts.query}" if parts.query else "")


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(CHUNK):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


class Proxy:
    """The proxy, on its own thread and event loop: the bytes of the
    downloads never wait on the API's loop, nor it on them."""

    def __init__(self, resolver=resolve, name: str = "egress"):
        """name: the app's, which its refusals start with ("songdrop: … not the internet")."""
        self._resolve = resolver
        self.name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.Server | None = None
        self._ready = threading.Event()
        self._open = 0
        self.url = ""
        self.refused = 0
        self.connections = 0

    def start(self) -> str:
        """Starts it on 127.0.0.1, a free port; its URL ("http://127.0.0.1:43121")."""
        thread = threading.Thread(target=self._run, name="egress", daemon=True)
        thread.start()
        if not self._ready.wait(10) or not self.url:
            raise RuntimeError("the egress proxy didn't start")
        return self.url

    def stop(self) -> None:
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._server = self._loop.run_until_complete(asyncio.start_server(self._serve, "127.0.0.1", 0,
                                                                             limit=HEAD_LIMIT))
            port = self._server.sockets[0].getsockname()[1]
            self.url = f"http://127.0.0.1:{port}"
        finally:
            self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _serve(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        if self._open >= MAX_OPEN:
            await self._answer(client_writer, 503, "too many connections at once")
            return
        self._open += 1
        try:
            await self._handle(client_reader, client_writer)
        except Exception as exc:  # one connection's trouble is its own
            log.debug("egress connection failed: %r", exc)
        finally:
            self._open -= 1
            client_writer.close()

    async def _handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(client_reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, host, port, path = _target(lines[0])
        except ValueError as exc:
            await self._answer(client_writer, 400, str(exc))
            return
        started = time.monotonic()
        try:
            addresses = await self._resolve(host, port)
            upstream_reader, upstream_writer = await self._connect(addresses, port)
        except Refused as exc:
            self.refused += 1
            log.warning("refused a connection to %s:%s: %s", host, port, exc)
            await self._answer(client_writer, 403, str(exc))
            return
        except (ConnectionError, OSError, TimeoutError) as exc:
            await self._answer(client_writer, 502, str(exc) or f"couldn't reach {host}")
            return
        self.connections += 1
        log.debug("%s %s:%s in %.0f ms", method, host, port, (time.monotonic() - started) * 1000)
        try:
            if path is None:
                client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await client_writer.drain()
            else:
                # The site's request: origin-form, without what was for the proxy, one per connection.
                kept = [line for line in lines[1:] if line and line.split(":", 1)[0].strip().lower() not in _HOP]
                request = "\r\n".join([f"{method} {path} {lines[0].rsplit(' ', 1)[1]}", *kept, "Connection: close",
                                       "", ""])
                upstream_writer.write(request.encode("latin-1"))
                await upstream_writer.drain()
            # Done when the site is: a client that keeps its end open doesn't keep the connection.
            sending = asyncio.ensure_future(_pipe(client_reader, upstream_writer))
            await _pipe(upstream_reader, client_writer)
            sending.cancel()
        finally:
            upstream_writer.close()

    @staticmethod
    async def _connect(addresses: list[str], port: int):
        last: Exception | None = None
        for address in addresses:
            try:
                return await asyncio.wait_for(asyncio.open_connection(address, port, limit=CHUNK), CONNECT_TIMEOUT)
            except (OSError, TimeoutError) as exc:
                last = exc
        raise ConnectionError(f"couldn't connect: {last}")

    async def _answer(self, writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        text = reason.replace("\r", " ").replace("\n", " ")[:300]
        phrase = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway", 503: "Service Unavailable"}[status]
        body = f"{self.name}: {text}\n".encode()
        try:
            writer.write(f"HTTP/1.1 {status} {phrase}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
                         f"Connection: close\r\nX-Egress-Refused: {text}\r\n\r\n".encode("latin-1", "replace") + body)
            await writer.drain()
        except (ConnectionError, OSError):
            pass


# ---- in the download and link-check processes --------------------------------------------------


def environment(proxy: str) -> dict[str, str]:
    """The variables that send a process's (and its children's: ffmpeg,
    Deno) web requests through the proxy."""
    return {"http_proxy": proxy, "https_proxy": proxy, "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
            "ALL_PROXY": proxy, "all_proxy": proxy, "NO_PROXY": "", "no_proxy": ""}


# ffmpeg, when it downloads: the web, through the proxy (httpproxy is its CONNECT); no file, rtmp, rtsp, udp…
FFMPEG_PROTOCOLS = "http,https,tls,tcp,httpproxy,crypto,data"


def lock(proxy: str, name: str = "egress") -> None:
    """From now on, this process's sockets connect to the proxy only (and to
    local Unix sockets): a library that ignores the proxy fails instead of
    reaching out. DNS lookups stay (they only say a name)."""
    parts = urllib.parse.urlsplit(proxy)
    allowed = (parts.hostname, parts.port)
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check(sock: socket.socket, address) -> None:
        if sock.family == getattr(socket, "AF_UNIX", None):
            return
        if not isinstance(address, tuple) or (address[0], address[1]) != allowed:
            raise ConnectionRefusedError(f"{name}: connections go through its proxy, not to {address!r}")

    def connect(sock, address):
        check(sock, address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(sock, address)
        return original_connect_ex(sock, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
