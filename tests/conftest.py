"""Test harness: no real network, a local TLS stand-in for api.site-shot.com and full log capture."""

import datetime
import io
import ipaddress
import logging
import os
import socket
import ssl
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import dify_plugin  # noqa: F401  # gevent patching first, as in the plugin runtime
import gevent
import gevent.socket
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from dify_plugin.config.config import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from gevent.pool import Pool
from gevent.server import StreamServer
from PIL import Image, ImageDraw

import site_shot_api

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_TLS_CONTEXT = site_shot_api._TLS_CONTEXT

Respond = Callable[[socket.socket], None]


# --- Network guard -----------------------------------------------------------------------------


class NetworkGuard:
    """Routes api.site-shot.com:443 to a local server; every other lookup or connection fails."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, int], tuple[str, int]] = {}
        self.blocked: list[object] = []


@pytest.fixture(autouse=True)
def network(monkeypatch: pytest.MonkeyPatch) -> Iterator[NetworkGuard]:
    guard = NetworkGuard()
    real_connect = socket.socket.connect

    def getaddrinfo(host: str, port: int, *_args: object, **_kwargs: object) -> list[tuple]:
        target = guard.routes.get((host, port))
        if target is None:
            guard.blocked.append((host, port))
            raise socket.gaierror(socket.EAI_NONAME, "DNS is blocked in tests")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", target)]

    def connect(sock: socket.socket, address: object) -> None:
        if not (isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback):
            guard.blocked.append(address)
            raise ConnectionRefusedError("the network is blocked in tests")
        real_connect(sock, address)

    # gevent's create_connection resolves through gevent.socket.getaddrinfo, not socket.getaddrinfo.
    monkeypatch.setattr(gevent.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    yield guard
    assert guard.blocked == [], f"a test tried to reach the network: {guard.blocked}"


# --- Test TLS certificates ---------------------------------------------------------------------


def _certificate(
    key: ec.EllipticCurvePrivateKey, name: str, issuer_key: ec.EllipticCurvePrivateKey, issuer: str, host: str | None
) -> x509.Certificate:
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=host is None, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
    )
    if host is None:
        usage = dict.fromkeys(("content_commitment", "key_encipherment", "data_encipherment", "key_agreement"), False)
        usage |= {"encipher_only": False, "decipher_only": False}
        builder = builder.add_extension(
            x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, **usage), critical=True
        )
    else:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


@dataclass(frozen=True)
class TestTls:
    client_context: ssl.SSLContext  # trusts only the test CA and checks host names
    server_context: ssl.SSLContext  # certificate for api.site-shot.com from the test CA
    wrong_host_context: ssl.SSLContext  # certificate for another host from the same test CA


@pytest.fixture(scope="session")
def tls(tmp_path_factory: pytest.TempPathFactory) -> TestTls:
    directory = tmp_path_factory.mktemp("tls")
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _certificate(ca_key, "Site-Shot plugin test CA", ca_key, "Site-Shot plugin test CA", None)

    def server_context(host: str) -> ssl.SSLContext:
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = _certificate(key, host, ca_key, "Site-Shot plugin test CA", host)
        path = directory / f"{host}.pem"
        path.write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
            + key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(path)
        return context

    client_context = ssl.create_default_context(cadata=ca.public_bytes(serialization.Encoding.PEM).decode())
    return TestTls(client_context, server_context(site_shot_api.API_HOST), server_context("other.example"))


# --- Local stand-in for the Site-Shot API --------------------------------------------------------


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: list[tuple[str, str]]
    headers: dict[str, str]  # lower-case names


class FakeSiteShot:
    """A local server for api.site-shot.com:443. Without a TLS context it is a TCP peer that never speaks TLS."""

    def __init__(self, context: ssl.SSLContext | None) -> None:
        self.context = context
        self.connections = 0
        self.requests: list[Request] = []
        self.respond: Respond = send(http_response(500))
        # A pool lets close() kill handlers that are still sending, so none outlives its test.
        self._server = StreamServer(("127.0.0.1", 0), self._handle, spawn=Pool())
        self._server.start()

    @property
    def address(self) -> tuple[str, int]:
        return "127.0.0.1", self._server.server_port

    def close(self) -> None:
        self._server.stop(timeout=0)

    def _handle(self, connection: socket.socket, _address: object) -> None:
        self.connections += 1
        try:
            if self.context is None:
                self.respond(connection)
                return
            with self.context.wrap_socket(connection, server_side=True) as tls_connection:
                head = b""
                while b"\r\n\r\n" not in head:
                    data = tls_connection.recv(65536)
                    if not data:
                        return
                    head += data
                self.requests.append(_parse_request(head.split(b"\r\n\r\n", 1)[0]))
                self.respond(tls_connection)
        except OSError:
            pass  # the client gave up first, which the timeout and TLS tests intend


def _parse_request(head: bytes) -> Request:
    request_line, *header_lines = head.decode("latin-1").split("\r\n")
    method, target, _version = request_line.split(" ")
    parts = urlsplit(target)
    headers = {}
    for line in header_lines:
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return Request(method, parts.path, parse_qsl(parts.query, keep_blank_values=True), headers)


def send(*steps: bytes | float) -> Respond:
    """Write the byte steps in order and sleep for the float steps; the connection closes afterwards."""

    def respond(connection: socket.socket) -> None:
        for step in steps:
            if isinstance(step, bytes):
                connection.sendall(step)
            else:
                gevent.sleep(step)

    return respond


def http_response(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> bytes:
    """An HTTP/1.1 answer; a header given an empty value is left out."""
    fields = {"Content-Length": str(len(body)), "Connection": "close", **(headers or {})}
    head = f"HTTP/1.1 {status} Test\r\n" + "".join(f"{name}: {value}\r\n" for name, value in fields.items() if value)
    return (head + "\r\n").encode("latin-1") + body


def chunked(body: bytes, size: int = 4096) -> bytes:
    pieces = [body[start : start + size] for start in range(0, len(body), size)]
    return b"".join(f"{len(piece):x}\r\n".encode() + piece + b"\r\n" for piece in pieces) + b"0\r\n\r\n"


def screenshot(image_format: str, width: int = 320, height: int = 200) -> bytes:
    """A synthetic page-like image; the tests never use a real capture."""
    image = Image.new("RGB", (width, height), (245, 247, 250))
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, width - 16, 48), fill=(41, 128, 185))
    for top in range(64, height - 16, 20):
        draw.line((16, top, width - 64, top), fill=(60, 60, 60), width=6)
    buffer = io.BytesIO()
    image.save(buffer, site_shot_api.FORMATS[image_format].pillow_format)
    return buffer.getvalue()


@pytest.fixture
def api(network: NetworkGuard, tls: TestTls, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSiteShot]:
    server = FakeSiteShot(tls.server_context)
    network.routes[(site_shot_api.API_HOST, 443)] = server.address
    monkeypatch.setattr(site_shot_api, "_TLS_CONTEXT", tls.client_context)
    yield server
    server.close()


# --- Log capture ---------------------------------------------------------------------------------


class _Collect(logging.Handler):
    def __init__(self, records: list[str]) -> None:
        super().__init__(logging.DEBUG)
        self.records = records
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))  # includes the formatted arguments and any traceback


@pytest.fixture
def logs() -> Iterator[list[str]]:
    """Every record at DEBUG and above from every logger, including loggers that do not propagate."""
    records: list[str] = []
    handler = _Collect(records)
    loggers = [logging.getLogger()] + [
        logger for logger in logging.Logger.manager.loggerDict.values() if isinstance(logger, logging.Logger)
    ]
    saved = [(logger, logger.level, logger.disabled) for logger in loggers]
    disabled_level = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    for logger in loggers:
        logger.setLevel(logging.DEBUG)
        logger.disabled = False
        logger.addHandler(handler)
    yield records
    for logger, level, disabled in saved:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.disabled = disabled
    logging.disable(disabled_level)


# --- The plugin as the SDK loads it --------------------------------------------------------------


@pytest.fixture(scope="session")
def registration() -> PluginRegistration:
    previous = os.getcwd()
    os.chdir(PLUGIN_ROOT)  # the SDK reads manifest.yaml, the declarations and _assets from the working directory
    try:
        return PluginRegistration(DifyPluginEnv())
    finally:
        os.chdir(previous)


@pytest.fixture(scope="session")
def provider_class(registration: PluginRegistration) -> type:
    return registration.tools_mapping["site_shot"][1]


@pytest.fixture(scope="session")
def tool_class(registration: PluginRegistration) -> type:
    return registration.tools_mapping["site_shot"][2]["capture_screenshot"][1]
