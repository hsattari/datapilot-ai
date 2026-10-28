"""Safe outbound fetch for URL connectors (SSRF protection).

The server fetches a URL that a customer typed, so it must never be turned into a way to reach our own network,
cloud metadata services or other customers. Rules, checked again on every hop of every fetch (not only when the
URL is saved):

- https only on port 443 (plain http on port 80 only when explicitly allowed, for local development),
- no user name or password inside the URL, no empty or oversized URL,
- host names that are local by convention (localhost, .local, .internal, ...) are refused,
- the name is resolved here and EVERY address must be a public one (not private, loopback, link-local, multicast,
  reserved, carrier-grade NAT, or an IPv4 address wrapped in IPv6); the connection then goes to that checked
  address, so a name that answers differently a moment later (DNS rebinding) cannot redirect it,
- redirects are followed by hand, at most 3, each one validated like a new URL,
- no proxy or environment settings are honoured, a hard size cap applies to the decoded body, and a time limit to
  the whole fetch.
"""
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

# httpx logs every request URL at INFO, and the query string may be a pre-signed token: keep it out of the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

MAX_URL_LEN = 2000
MAX_REDIRECTS = 3
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".localdomain", ".lan", ".home", ".corp", ".intranet")


class UrlRejected(Exception):
    """The address is not allowed or could not be fetched; the message is safe to show to the person."""


@dataclass
class Fetched:
    data: bytes
    final_url: str
    content_type: str


def _public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def resolve(host: str, port: int) -> list[str]:
    """Every address the name resolves to (replaced in tests)."""
    try:
        return sorted({i[4][0] for i in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror:
        raise UrlRejected("The address could not be found.") from None


def check_url(url: str, allow_http: bool = False) -> tuple[str, str, int]:
    """Validate the URL text only (no network). Returns (scheme, host, port)."""
    if not url or len(url) > MAX_URL_LEN or any(ord(c) < 33 for c in url):
        raise UrlRejected("Enter a web address (https://...) without spaces.")
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        raise UrlRejected("That is not a valid web address.") from None
    if u.scheme not in ("https", "http") or (u.scheme == "http" and not allow_http):
        raise UrlRejected("Only https:// addresses are allowed.")
    if u.username or u.password or "@" in (u.netloc or ""):
        raise UrlRejected("Do not put a user name or password in the address.")
    host = (u.hostname or "").strip(".").lower()
    if not host:
        raise UrlRejected("That is not a valid web address.")
    default = 443 if u.scheme == "https" else 80
    if port not in (None, default):
        raise UrlRejected(f"Only the standard port ({default}) is allowed.")
    if host == "localhost" or host.endswith(BLOCKED_SUFFIXES) or "." not in host and ":" not in host:
        raise UrlRejected("Addresses on a private or local network are not allowed.")
    return u.scheme, host, default


def check_host(host: str, port: int) -> str:
    """Resolve and require that every address is public. Returns the address to connect to."""
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        addrs = resolve(host, port)
    if not addrs or not all(_public(a) for a in addrs):
        raise UrlRejected("Addresses on a private or local network are not allowed.")
    return addrs[0]


def fetch(url: str, *, max_bytes: int, timeout: float = 20.0, allow_http: bool = False,
          transport: httpx.BaseTransport | None = None) -> Fetched:
    deadline = time.monotonic() + timeout
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        scheme, host, port = check_url(current, allow_http)
        ip = check_host(host, port)
        u = httpx.URL(current)
        target = u.copy_with(host=ip)
        headers = {"Host": host, "User-Agent": "DataPilotAI-connector/1", "Accept": "*/*"}
        client = httpx.Client(transport=transport or httpx.HTTPTransport(), follow_redirects=False,
                              timeout=httpx.Timeout(timeout), trust_env=False)
        try:
            with client.stream("GET", target, headers=headers, extensions={"sni_hostname": host}) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc:
                        raise UrlRejected("The server redirected without saying where.")
                    current = urljoin(current, loc)
                    continue
                if r.status_code != 200:
                    raise UrlRejected(f"The server answered {r.status_code}; expected a file.")
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise UrlRejected(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise UrlRejected(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
                    if time.monotonic() > deadline:
                        raise UrlRejected("The server took too long to send the file.")
                return Fetched(bytes(buf), current, (r.headers.get("content-type") or "").split(";")[0].strip().lower())
        except UrlRejected:
            raise
        except httpx.TimeoutException:
            raise UrlRejected("The server took too long to answer.") from None
        except httpx.HTTPError:
            raise UrlRejected("The server could not be reached.") from None
        finally:
            client.close()
    raise UrlRejected(f"Too many redirects (more than {MAX_REDIRECTS}).")
