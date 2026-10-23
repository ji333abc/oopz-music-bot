"""Bounded loopback relay for Bilibili audio requiring a Referer header."""

from __future__ import annotations

import atexit
import re
import secrets
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import requests

_REFERER = "https://www.bilibili.com/"
_RANGE = re.compile(r"bytes=(\d{0,20})-(\d{0,20})")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


def validate_audio_url(url: str) -> str:
    """Only platform-issued HTTPS CDN URLs may enter the relay."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not (
            any(
                host == suffix or host.endswith("." + suffix)
                for suffix in ("bilivideo.com", "bilivideo.cn")
            )
            or host == "upos-hz-mirrorakam.akamaized.net"
        )
    ):
        raise ValueError("B站返回了不支持的音频 CDN 地址")
    return url


class AudioStreamProxy:
    """Relay current audio with Range support, without buffering entire files."""

    def __init__(self, *, capacity: int = 32, ttl: float = 6 * 3600, getter=None):
        self._capacity = capacity
        self._ttl = ttl
        self._getter = getter or self._fetch
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._slots = threading.BoundedSemaphore(4)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                owner._handle(self, head=False)

            def do_HEAD(self):
                owner._handle(self, head=True)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="bilibili-audio-relay",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _fetch(url, **kwargs):
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(url, **kwargs)
        except Exception:
            session.close()
            raise
        original_close = response.close

        def close():
            try:
                original_close()
            finally:
                session.close()

        response.close = close
        return response

    def register(self, url: str) -> str:
        validate_audio_url(url)
        now = time.monotonic()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._entries = OrderedDict(
                (key, value) for key, value in self._entries.items() if value[0] > now
            )
            self._entries[token] = (now + self._ttl, url)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
        return f"http://127.0.0.1:{self._server.server_port}/{token}"

    def _handle(self, handler, *, head: bool):
        token = handler.path.removeprefix("/")
        with self._lock:
            entry = self._entries.get(token)
        if entry is None or entry[0] <= time.monotonic():
            handler.send_error(404)
            return
        headers = {"Referer": _REFERER, "User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        requested_range = handler.headers.get("Range")
        if requested_range:
            match = _RANGE.fullmatch(requested_range)
            if not match or not any(match.groups()):
                handler.send_error(416)
                return
            start, end = match.groups()
            if start and end and int(end) < int(start):
                handler.send_error(416)
                return
            headers["Range"] = requested_range
        if not self._slots.acquire(blocking=False):
            handler.send_error(503)
            return
        started = False
        try:
            url = entry[1]
            for _attempt in range(4):
                validate_audio_url(url)
                response = self._getter(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=(5, 20),
                    allow_redirects=False,
                )
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                from urllib.parse import urljoin

                location = response.headers.get("Location", "")
                response.close()
                if not location:
                    raise ValueError("CDN redirect has no destination")
                url = urljoin(url, location)
            else:
                raise ValueError("Too many CDN redirects")
            with response:
                if response.status_code not in (200, 206, 416):
                    handler.send_error(502)
                    return
                handler.send_response(response.status_code)
                for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                    if response.headers.get(name):
                        handler.send_header(name, response.headers[name])
                handler.send_header("Cache-Control", "no-store")
                handler.send_header("Access-Control-Allow-Origin", "*")
                handler.send_header("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges")
                handler.end_headers()
                started = True
                if not head:
                    for chunk in response.iter_content(64 * 1024):
                        if chunk:
                            handler.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (requests.RequestException, ValueError, OSError):
            if not started:
                handler.send_error(502)
            handler.close_connection = True
        finally:
            self._slots.release()

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        with self._lock:
            self._entries.clear()


_proxy: AudioStreamProxy | None = None
_proxy_lock = threading.Lock()


def relay_audio(url: str) -> str:
    global _proxy
    with _proxy_lock:
        if _proxy is None:
            _proxy = AudioStreamProxy()
            atexit.register(_proxy.close)
        return _proxy.register(url)
