from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import time
from collections.abc import Sequence
from dataclasses import dataclass
from html import unescape
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any
from urllib.parse import urljoin, urlsplit

from .models import Evidence

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.-]{2,}")
_TAG = re.compile(r"<[^>]+>")
_STOP_WORDS = frozenset({"and", "for", "from", "into", "that", "the", "with", "you", "your"})
_MAX_JOB_PAGE_BYTES = 2 * 1024 * 1024
_ALLOWED_JOB_CONTENT_TYPES = frozenset(
    {"application/json", "application/xhtml+xml", "text/html", "text/plain"}
)
_JOB_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_MAX_JOB_REDIRECTS = 5
_JOB_FETCH_TIMEOUT_SECONDS = 30


class _JobFetchDeadlineExceeded(TimeoutError):
    """Raised when the single wall-clock budget for a job-page fetch is exhausted."""


class _FetchCancellation:
    """Track live network resources so the caller can interrupt a blocked worker."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._cancelled = False
        self._sockets: list[socket.socket] = []
        self._connections: list[HTTPConnection] = []

    @staticmethod
    def _shutdown_socket(network_socket: socket.socket) -> None:
        try:
            network_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            network_socket.close()
        except OSError:
            pass

    def register_socket(self, network_socket: socket.socket) -> None:
        with self._lock:
            cancelled = self._cancelled
            if not cancelled:
                self._sockets.append(network_socket)
        if cancelled:
            self._shutdown_socket(network_socket)
            raise _JobFetchDeadlineExceeded("job page fetch exceeded the 30 second deadline")

    def replace_socket(
        self,
        old_socket: socket.socket,
        new_socket: socket.socket,
    ) -> None:
        with self._lock:
            self._sockets = [item for item in self._sockets if item is not old_socket]
            cancelled = self._cancelled
            if not cancelled:
                self._sockets.append(new_socket)
        if cancelled:
            self._shutdown_socket(new_socket)
            raise _JobFetchDeadlineExceeded("job page fetch exceeded the 30 second deadline")

    def unregister_socket(self, network_socket: socket.socket) -> None:
        with self._lock:
            self._sockets = [item for item in self._sockets if item is not network_socket]

    def register_connection(self, connection: HTTPConnection) -> None:
        with self._lock:
            cancelled = self._cancelled
            if not cancelled:
                self._connections.append(connection)
        if cancelled:
            connection.close()
            raise _JobFetchDeadlineExceeded("job page fetch exceeded the 30 second deadline")

    def unregister_connection(self, connection: HTTPConnection) -> None:
        with self._lock:
            self._connections = [item for item in self._connections if item is not connection]

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            sockets = self._sockets
            connections = self._connections
            self._sockets = []
            self._connections = []
        for network_socket in sockets:
            self._shutdown_socket(network_socket)
        for connection in connections:
            try:
                connection.close()
            except OSError:
                pass


@dataclass(frozen=True)
class _ResolvedEndpoint:
    family: int
    socket_type: int
    protocol: int
    socket_address: Any


@dataclass(frozen=True)
class _ResolvedJobURL:
    scheme: str
    hostname: str
    port: int
    request_target: str
    endpoints: tuple[_ResolvedEndpoint, ...]


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _JobFetchDeadlineExceeded("job page fetch exceeded the 30 second deadline")
    return remaining


def _getaddrinfo_before_deadline(
    hostname: str,
    port: int,
    deadline: float | None,
) -> list[Any]:
    if deadline is None:
        return socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)

    _remaining_timeout(deadline)
    result_queue: Queue[Any] = Queue(maxsize=1)

    def resolve() -> None:
        try:
            result_queue.put((socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM), None))
        except Exception as error:
            result_queue.put((None, error))

    Thread(
        target=resolve,
        name="recruiting-pipeline-dns",
        daemon=True,
    ).start()
    try:
        addresses, error = result_queue.get(timeout=_remaining_timeout(deadline))
    except Empty as error:
        raise _JobFetchDeadlineExceeded("job page fetch exceeded the 30 second deadline") from error
    if error is not None:
        raise error
    _remaining_timeout(deadline)
    return addresses


def _resolve_public_job_url(
    job_url: str,
    *,
    deadline: float | None = None,
) -> _ResolvedJobURL:
    """Resolve an HTTP(S) URL once and retain only its validated numeric endpoints."""
    parsed = urlsplit(job_url)
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError("job URL must use HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("job URL must not contain embedded credentials")
    if parsed.hostname is None:
        raise ValueError("job URL must include a hostname")
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("job URL must resolve to a public host")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as error:
        raise ValueError("job URL contains an invalid port") from error
    try:
        addresses = _getaddrinfo_before_deadline(hostname, port, deadline)
    except socket.gaierror as error:
        raise ValueError("job URL hostname could not be resolved") from error
    if not addresses:
        raise ValueError("job URL hostname could not be resolved")

    endpoints: list[_ResolvedEndpoint] = []
    for family, socket_type, protocol, _, socket_address in addresses:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise ValueError("job URL resolved to an invalid network address")
        raw_address = str(socket_address[0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as error:
            raise ValueError("job URL resolved to an invalid network address") from error
        if not address.is_global:
            raise ValueError("job URL must resolve only to public network addresses")
        endpoints.append(
            _ResolvedEndpoint(
                family=family,
                socket_type=socket_type,
                protocol=protocol,
                socket_address=socket_address,
            )
        )

    request_target = parsed.path or "/"
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"
    return _ResolvedJobURL(
        scheme=scheme,
        hostname=hostname,
        port=port,
        request_target=request_target,
        endpoints=tuple(endpoints),
    )


def _validate_public_job_url(job_url: str) -> str:
    """Validate an HTTP(S) URL and reject hosts that can reach private services."""
    _resolve_public_job_url(job_url)
    return job_url


def _set_socket_deadline_timeout(network_socket: socket.socket, deadline: float) -> None:
    network_socket.settimeout(_remaining_timeout(deadline))


def _connect_to_endpoint(
    endpoint: _ResolvedEndpoint,
    deadline: float,
    cancellation: _FetchCancellation,
) -> socket.socket:
    """Open a socket directly to a previously validated numeric endpoint."""
    _remaining_timeout(deadline)
    network_socket = socket.socket(endpoint.family, endpoint.socket_type, endpoint.protocol)
    cancellation.register_socket(network_socket)
    try:
        _set_socket_deadline_timeout(network_socket, deadline)
        network_socket.connect(endpoint.socket_address)
        _set_socket_deadline_timeout(network_socket, deadline)
    except Exception:
        cancellation.unregister_socket(network_socket)
        network_socket.close()
        raise
    return network_socket


def _connect_to_endpoints(
    endpoints: tuple[_ResolvedEndpoint, ...],
    deadline: float,
    cancellation: _FetchCancellation,
) -> socket.socket:
    last_error: OSError | None = None
    for endpoint in endpoints:
        try:
            return _connect_to_endpoint(endpoint, deadline, cancellation)
        except _JobFetchDeadlineExceeded:
            raise
        except OSError as error:
            last_error = error
    if last_error is None:
        raise OSError("job URL did not resolve to a usable network address")
    raise last_error


class _PinnedHTTPConnection(HTTPConnection):
    """HTTP connection whose socket target cannot be changed by a second DNS lookup."""

    def __init__(
        self,
        resolved_url: _ResolvedJobURL,
        deadline: float,
        cancellation: _FetchCancellation,
    ) -> None:
        super().__init__(
            resolved_url.hostname,
            resolved_url.port,
            timeout=_JOB_FETCH_TIMEOUT_SECONDS,
        )
        self._endpoints = resolved_url.endpoints
        self._deadline = deadline
        self._cancellation = cancellation

    def connect(self) -> None:
        self.sock = _connect_to_endpoints(
            self._endpoints,
            self._deadline,
            self._cancellation,
        )


class _PinnedHTTPSConnection(HTTPSConnection):
    """HTTPS connection pinned to an IP while authenticating the original hostname."""

    def __init__(
        self,
        resolved_url: _ResolvedJobURL,
        deadline: float,
        cancellation: _FetchCancellation,
    ) -> None:
        self._tls_context = ssl.create_default_context()
        super().__init__(
            resolved_url.hostname,
            resolved_url.port,
            timeout=_JOB_FETCH_TIMEOUT_SECONDS,
            context=self._tls_context,
        )
        self._endpoints = resolved_url.endpoints
        self._deadline = deadline
        self._cancellation = cancellation

    def connect(self) -> None:
        last_error: OSError | None = None
        for endpoint in self._endpoints:
            network_socket: socket.socket | None = None
            secured_socket: socket.socket | None = None
            try:
                network_socket = _connect_to_endpoint(
                    endpoint,
                    self._deadline,
                    self._cancellation,
                )
                _set_socket_deadline_timeout(network_socket, self._deadline)
                secured_socket = self._tls_context.wrap_socket(
                    network_socket,
                    server_hostname=self.host,
                )
                self._cancellation.replace_socket(network_socket, secured_socket)
                _set_socket_deadline_timeout(secured_socket, self._deadline)
            except _JobFetchDeadlineExceeded:
                if secured_socket is not None:
                    self._cancellation.unregister_socket(secured_socket)
                    secured_socket.close()
                elif network_socket is not None:
                    self._cancellation.unregister_socket(network_socket)
                    network_socket.close()
                raise
            except OSError as error:
                if secured_socket is not None:
                    self._cancellation.unregister_socket(secured_socket)
                    secured_socket.close()
                elif network_socket is not None:
                    self._cancellation.unregister_socket(network_socket)
                    network_socket.close()
                last_error = error
                continue
            self.sock = secured_socket
            return
        if last_error is None:
            raise OSError("job URL did not resolve to a usable network address")
        raise last_error


def _request_job_page(
    resolved_url: _ResolvedJobURL,
    deadline: float,
    cancellation: _FetchCancellation,
) -> tuple[HTTPConnection, HTTPResponse, socket.socket]:
    if resolved_url.scheme == "https":
        connection: HTTPConnection = _PinnedHTTPSConnection(
            resolved_url,
            deadline,
            cancellation,
        )
    else:
        connection = _PinnedHTTPConnection(resolved_url, deadline, cancellation)
    cancellation.register_connection(connection)
    active_socket: socket.socket | None = None
    try:
        connection.connect()
        active_socket = connection.sock
        if active_socket is None:
            raise OSError("job page connection did not create a socket")
        _set_socket_deadline_timeout(active_socket, deadline)
        connection.request(
            "GET",
            resolved_url.request_target,
            headers={"User-Agent": "recruiting-pipeline/0.1"},
        )
        _set_socket_deadline_timeout(active_socket, deadline)
        response = connection.getresponse()
        _remaining_timeout(deadline)
        return connection, response, active_socket
    except Exception:
        if active_socket is not None:
            cancellation.unregister_socket(active_socket)
        cancellation.unregister_connection(connection)
        connection.close()
        raise


def _read_job_payload(
    response: HTTPResponse,
    network_socket: socket.socket,
    deadline: float,
) -> str:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].casefold()
    if content_type not in _ALLOWED_JOB_CONTENT_TYPES:
        raise ValueError("job page must return HTML, plain text, or JSON")
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > _MAX_JOB_PAGE_BYTES:
            raise ValueError("job page exceeds the 2 MiB snapshot limit")
    _set_socket_deadline_timeout(network_socket, deadline)
    payload = response.read(_MAX_JOB_PAGE_BYTES + 1)
    _remaining_timeout(deadline)
    if len(payload) > _MAX_JOB_PAGE_BYTES:
        raise ValueError("job page exceeds the 2 MiB snapshot limit")
    charset = response.headers.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _terms(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(text) if word.casefold() not in _STOP_WORDS}


def _fetch_job_snapshot_with_deadline(
    job_url: str,
    deadline: float,
    cancellation: _FetchCancellation,
) -> str:
    current_url = job_url
    redirects_followed = 0
    while True:
        resolved_url = _resolve_public_job_url(current_url, deadline=deadline)
        connection, response, network_socket = _request_job_page(
            resolved_url,
            deadline,
            cancellation,
        )
        try:
            if response.status in _JOB_REDIRECT_CODES:
                if redirects_followed >= _MAX_JOB_REDIRECTS:
                    raise ValueError("job page exceeded the 5 redirect limit")
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("job page redirect did not include a location")
                current_url = urljoin(current_url, location)
                redirects_followed += 1
                continue
            if 300 <= response.status < 400:
                raise ValueError(f"job page returned unsupported redirect HTTP {response.status}")
            if response.status >= 400:
                raise ValueError(f"job page returned HTTP {response.status}")
            html = _read_job_payload(response, network_socket, deadline)
            break
        finally:
            cancellation.unregister_socket(network_socket)
            cancellation.unregister_connection(connection)
            response.close()
            connection.close()
    text = " ".join(unescape(_TAG.sub(" ", html)).split())
    if not text:
        raise ValueError("job page did not contain readable text")
    return text


def fetch_job_snapshot(job_url: str) -> str:
    """Retrieve a job page as untrusted text within one 30-second deadline.

    Direct pinned sockets intentionally ignore ambient HTTP proxy variables for SSRF safety.
    """
    deadline = time.monotonic() + _JOB_FETCH_TIMEOUT_SECONDS
    cancellation = _FetchCancellation()
    result_queue: Queue[Any] = Queue(maxsize=1)

    def fetch() -> None:
        try:
            result_queue.put(
                (
                    True,
                    _fetch_job_snapshot_with_deadline(job_url, deadline, cancellation),
                )
            )
        except BaseException as error:
            result_queue.put((False, error))

    Thread(
        target=fetch,
        name="recruiting-pipeline-job-fetch",
        daemon=True,
    ).start()
    try:
        succeeded, result = result_queue.get(timeout=_remaining_timeout(deadline))
    except (Empty, _JobFetchDeadlineExceeded) as error:
        cancellation.cancel()
        raise _JobFetchDeadlineExceeded("job page fetch exceeded the 30 second deadline") from error
    if succeeded:
        return result
    raise result


def select_relevant_evidence(job_description: str, evidence: Sequence[Evidence]) -> list[Evidence]:
    """Rank approved, user-provided evidence by transparent lexical overlap only."""
    job_terms = _terms(job_description)
    scored = [(len(job_terms & _terms(item.text)), item) for item in evidence if item.approved]
    return [
        item for score, item in sorted(scored, key=lambda pair: (-pair[0], pair[1].id)) if score
    ]
