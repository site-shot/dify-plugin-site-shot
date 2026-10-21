"""The shared HTTPS transport: one deadline for the whole exchange, certificate checks, no proxy, no retry."""

import socket
import time

import gevent
import gevent.socket
import pytest
from conftest import PRODUCTION_TLS_CONTEXT, FakeSiteShot, http_response, screenshot, send

import site_shot_api
from site_shot_api import SiteShotError

KEY = "test-key-Q7m2Zp9Kx4"
REQUEST = site_shot_api.CaptureRequest(
    "https://example.com/", site_shot_api.FORMATS["png"], 1024, 768, full_page=False, max_height=20000
)


def capture_with_deadline(monkeypatch, seconds=1):
    monkeypatch.setattr(site_shot_api, "CAPTURE_TIMEOUT", seconds)
    started = time.monotonic()
    with pytest.raises(SiteShotError, match=f"did not answer within {seconds} seconds. The request was not retried"):
        site_shot_api.capture(KEY, REQUEST)
    return time.monotonic() - started


def test_deadline_covers_a_server_that_never_answers(api, monkeypatch):
    api.respond = send(30.0)
    assert 1 <= capture_with_deadline(monkeypatch) < 1.5
    assert api.connections == 1


def test_deadline_covers_headers_that_trickle_in(api, monkeypatch):
    trickle = [step for _ in range(100) for step in (0.05, b"X-Pad: 1\r\n")]  # each read alone is quick
    api.respond = send(b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n", *trickle)
    assert 1 <= capture_with_deadline(monkeypatch) < 1.5
    assert api.connections == 1


def test_deadline_covers_a_body_that_trickles_in(api, monkeypatch):
    png = screenshot("png")
    head = http_response(200, png, {"Content-Type": "image/png"})[: -len(png)]
    trickle = [step for start in range(0, len(png), 10) for step in (0.05, png[start : start + 10])]  # > 3 s
    api.respond = send(head, *trickle)
    assert 1 <= capture_with_deadline(monkeypatch) < 1.5


def test_deadline_covers_a_tls_handshake_that_never_finishes(network, monkeypatch):
    silent = FakeSiteShot(context=None)
    silent.respond = send(30.0)
    network.routes[(site_shot_api.API_HOST, 443)] = silent.address
    try:
        assert 1 <= capture_with_deadline(monkeypatch) < 1.5
        assert silent.connections == 1
    finally:
        silent.close()


def test_deadline_covers_a_dns_lookup_that_never_finishes(api, monkeypatch):
    def slow_lookup(*_args, **_kwargs):
        gevent.sleep(30)

    monkeypatch.setattr(gevent.socket, "getaddrinfo", slow_lookup)
    assert 1 <= capture_with_deadline(monkeypatch) < 1.5
    assert api.connections == 0


def test_production_trust_store_rejects_a_certificate_it_does_not_know(api, monkeypatch):
    monkeypatch.setattr(site_shot_api, "_TLS_CONTEXT", PRODUCTION_TLS_CONTEXT)
    api.respond = send(http_response(200, screenshot("png"), {"Content-Type": "image/png"}))
    with pytest.raises(SiteShotError, match="TLS certificate of the Site-Shot API could not be verified"):
        site_shot_api.capture(KEY, REQUEST)
    assert api.connections == 1 and api.requests == []  # nothing, not even the key, was sent


def test_certificate_for_another_host_is_rejected(network, tls, monkeypatch):
    impostor = FakeSiteShot(tls.wrong_host_context)  # signed by the trusted test CA, but for other.example
    network.routes[(site_shot_api.API_HOST, 443)] = impostor.address
    monkeypatch.setattr(site_shot_api, "_TLS_CONTEXT", tls.client_context)
    try:
        with pytest.raises(SiteShotError, match="TLS certificate of the Site-Shot API could not be verified"):
            site_shot_api.check_credentials(KEY)
        assert impostor.requests == []
    finally:
        impostor.close()


def test_proxy_environment_is_ignored(api, network, monkeypatch):
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://proxy.invalid:3128")
    png = screenshot("png")
    api.respond = send(http_response(200, png, {"Content-Type": "image/png"}))
    assert site_shot_api.capture(KEY, REQUEST).content == png
    assert network.blocked == [] and api.connections == 1


def test_network_guard_blocks_everything_but_the_routed_test_server(network):
    with pytest.raises(SiteShotError, match="Could not connect to the Site-Shot API"):
        site_shot_api.check_credentials(KEY)
    with pytest.raises(OSError):
        socket.create_connection(("192.0.2.10", 443), timeout=1)
    with pytest.raises(OSError), socket.socket() as raw:
        raw.connect(("192.0.2.10", 443))
    assert network.blocked == [("api.site-shot.com", 443), ("192.0.2.10", 443), ("192.0.2.10", 443)]
    network.blocked.clear()  # these attempts were the point of this test
