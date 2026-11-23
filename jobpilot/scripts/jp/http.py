"""The one HTTP client for third-party data (job boards, postings, feeds).

Rules, the same as the app's `net::http`:
- http(s) only, public hosts only: nothing on this machine or a private network is read, by name or
  by address, and a name that resolves to a private address is refused (the connection is pinned to
  the address that was checked);
- at most one request per host per second;
- a size cap, checked while reading;
- redirects are followed by hand (at most 5), every hop is checked again, a POST is never redirected.

Behind an HTTP proxy the proxy resolves the name, so only the name and literal-address checks apply.
"""
import gzip
import http.client
import ipaddress
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from . import VERSION

USER_AGENT = f"JobPilot-skill/{VERSION} (personal job-search tool; read-only)"
HOST_DELAY = 1.0
TIMEOUT = 30
MAX_BYTES = 20 * 1024 * 1024
MAX_REDIRECTS = 5

NOT_PUBLIC = "addresses on this machine or a private network are not read"

# Tests only: lets the loopback fixtures be read. Never set by a command.
TEST_ALLOW_PRIVATE = False


class HttpError(Exception):
    """A request that failed. `status` is the HTTP status when the server answered."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Response:
    def __init__(self, url, status, headers, body):
        self.url = url
        self.status = status
        self.headers = headers
        self.body = body

    def text(self):
        charset = self.headers.get_content_charset() if hasattr(self.headers, "get_content_charset") else None
        for encoding in (charset, "utf-8"):
            if not encoding:
                continue
            try:
                return self.body.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        try:
            return json.loads(self.body)
        except ValueError:
            raise HttpError("the answer is not JSON")


def ipv4_is_public(ip):
    a, b, c, _ = ip.packed
    return not (
        a == 127  # loopback
        or a == 10 or (a == 172 and 16 <= b < 32) or (a == 192 and b == 168)  # private
        or (a == 169 and b == 254)  # link-local
        or a == 0
        or a >= 224  # multicast, reserved, broadcast
        or (a == 192 and b == 0 and c == 2) or (a == 198 and b == 51 and c == 100) or (a == 203 and b == 0 and c == 113)
        or (a == 100 and 64 <= b < 128)  # 100.64.0.0/10, carrier-grade NAT
        or (a == 192 and b == 0 and c == 0)  # IETF protocol assignments
        or (a == 198 and 18 <= b < 20)  # benchmarking
    )


def ipv6_is_public(ip):
    if ip.ipv4_mapped is not None:
        return ipv4_is_public(ip.ipv4_mapped)
    packed = ip.packed
    seg = [int.from_bytes(packed[i:i + 2], "big") for i in range(0, 16, 2)]

    def embedded(hi, lo):
        return ipv4_is_public(ipaddress.IPv4Address((hi << 16) | lo))

    if seg[0] == 0x64 and seg[1] == 0xFF9B:  # NAT64
        return seg[2:6] == [0, 0, 0, 0] and embedded(seg[6], seg[7])
    if seg[0] == 0x2002:  # 6to4
        return embedded(seg[1], seg[2])
    if seg[:6] == [0] * 6:  # IPv4-compatible, `::` and `::1`
        return embedded(seg[6], seg[7])
    documentation = seg[0] == 0x2001 and seg[1] == 0x0DB8
    unique_local = seg[0] & 0xFE00 == 0xFC00
    link_local = seg[0] & 0xFFC0 == 0xFE80
    site_local = seg[0] & 0xFFC0 == 0xFEC0
    return not (ip.is_multicast or documentation or unique_local or link_local or site_local)


def ip_is_public(ip):
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip)
    return ipv4_is_public(ip) if ip.version == 4 else ipv6_is_public(ip)


def is_public_host(host):
    """A host that may be read: a public address literal, or a dotted name that is not local."""
    host = (host or "").strip("[]")
    if not host:
        return False
    try:
        return ip_is_public(ipaddress.ip_address(host))
    except ValueError:
        pass
    name = host.rstrip(".").lower()
    return (
        "." in name
        and name != "localhost"
        and not name.endswith((".localhost", ".local", ".internal", ".lan"))
    )


def checked_url(raw, allow_private=False):
    """The URL if it is http(s), has a host and (unless `allow_private`) the host is public."""
    try:
        parts = urllib.parse.urlsplit(raw.strip())
        host = parts.hostname
    except ValueError:
        raise HttpError("not a valid address")
    if parts.scheme not in ("http", "https") or not host:
        raise HttpError("only http(s) addresses are read")
    if parts.username or parts.password:
        raise HttpError("addresses with a user name or password are not read")
    if not (allow_private or TEST_ALLOW_PRIVATE) and not is_public_host(host):
        raise HttpError(NOT_PUBLIC)
    return urllib.parse.urlunsplit(parts._replace(fragment=""))


def _public_address(host, port):
    """One address of `host` that is public; refuses a name that only resolves to private ones."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise HttpError("the host name could not be resolved")
    for info in infos:
        address = info[4][0]
        if ip_is_public(address.split("%")[0]):
            return address
    raise HttpError(NOT_PUBLIC)


class _PinnedHTTP(http.client.HTTPConnection):
    """Connects to the address that was checked, whatever the name resolves to later."""

    def __init__(self, *args, pinned=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pinned = pinned

    def connect(self):
        self.sock = socket.create_connection((self._pinned or self.host, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, *args, pinned=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pinned = pinned

    def connect(self):
        sock = socket.create_connection((self._pinned or self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _proxied(url):
    parts = urllib.parse.urlsplit(url)
    proxies = urllib.request.getproxies()
    return parts.scheme in proxies and not urllib.request.proxy_bypass(parts.hostname or "")


class Http:
    def __init__(self, host_delay=HOST_DELAY, timeout=TIMEOUT, max_bytes=MAX_BYTES, allow_private=False):
        self.host_delay = host_delay
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.allow_private = allow_private
        self._next = {}

    def _private_ok(self):
        return self.allow_private or TEST_ALLOW_PRIVATE

    def _wait_turn(self, host):
        now = time.monotonic()
        at = self._next.get(host, now)
        if at > now:
            time.sleep(at - now)
        self._next[host] = max(at, now) + self.host_delay

    def _opener(self, url):
        handlers = [_NoRedirect()]
        context = ssl.create_default_context()
        if _proxied(url) or self._private_ok():
            handlers.append(urllib.request.HTTPSHandler(context=context))
            if self._private_ok() and not _proxied(url):
                handlers.append(urllib.request.ProxyHandler({}))
            return urllib.request.build_opener(*handlers)
        parts = urllib.parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        pinned = _public_address(parts.hostname, port)

        class Plain(urllib.request.HTTPHandler):
            def http_open(self, req):
                return self.do_open(lambda host, **kw: _PinnedHTTP(host, pinned=pinned, **kw), req)

        class Secure(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return self.do_open(
                    lambda host, **kw: _PinnedHTTPS(host, pinned=pinned, **kw), req, context=context
                )

        handlers += [urllib.request.ProxyHandler({}), Plain(), Secure()]
        return urllib.request.build_opener(*handlers)

    def _read(self, response):
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            raise HttpError("the answer is too large")
        gz = (response.headers.get("Content-Encoding") or "").lower() == "gzip"
        inflate = zlib.decompressobj(16 + zlib.MAX_WBITS) if gz else None
        chunks, size = [], 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            if inflate:
                chunk = inflate.decompress(chunk)
            size += len(chunk)
            if size > self.max_bytes:
                raise HttpError("the answer is too large")
            chunks.append(chunk)
        return b"".join(chunks)

    def _once(self, method, url, headers, body):
        self._wait_turn(urllib.parse.urlsplit(url).hostname)
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept-Encoding", "gzip")
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with self._opener(url).open(request, timeout=self.timeout) as response:
                return Response(url, response.status, response.headers, self._read(response))
        except urllib.error.HTTPError as err:
            with err:
                location = err.headers.get("Location")
                if err.code in (301, 302, 303, 307, 308) and location:
                    return Response(url, err.code, err.headers, b"")
                raise HttpError(f"the server answered {err.code}", status=err.code)
        except HttpError:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException, zlib.error, gzip.BadGzipFile) as err:
            reason = getattr(err, "reason", err)
            raise HttpError(f"the request failed: {type(reason).__name__}")

    def request(self, method, url, headers=None, body=None):
        headers = dict(headers or {})
        url = checked_url(url, self._private_ok())
        for _ in range(MAX_REDIRECTS + 1):
            response = self._once(method, url, headers, body)
            if response.status not in (301, 302, 303, 307, 308):
                return response
            if method != "GET":
                raise HttpError("a redirect of a POST is not followed")
            target = checked_url(urllib.parse.urljoin(url, response.headers.get("Location")), self._private_ok())
            same_origin = urllib.parse.urlsplit(target)[:2] == urllib.parse.urlsplit(url)[:2]
            if not same_origin and any(name.lower() != "accept" for name in headers):
                raise HttpError("a redirect to another site is not followed with these headers")
            url = target
        raise HttpError("too many redirects")

    def get(self, url, accept=None):
        return self.request("GET", url, {"Accept": accept} if accept else {})

    def get_text(self, url, accept=None):
        return self.get(url, accept).text()

    def get_json(self, url):
        return self.get(url, "application/json").json()

    def post_json(self, url, payload):
        body = json.dumps(payload).encode()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        return self.request("POST", url, headers, body).json()


_shared = None


def client():
    """The process-wide client: one set of per-host delays for the whole command."""
    global _shared
    if _shared is None:
        _shared = Http()
    return _shared
