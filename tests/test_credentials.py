"""API key validation through the SDK provider: one header-only request that never captures."""

import time

import pytest
from conftest import http_response, send
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

import site_shot_api

KEY = "test-key-Q7m2Zp9Kx4"


def validate(provider_class, credentials):
    provider_class().validate_credentials(credentials)


def test_accepted_key_is_checked_with_a_header_only(api, provider_class):
    api.respond = send(http_response(200, b'{"status": "ok"}', {"Content-Type": "application/json"}))
    validate(provider_class, {"api_key": f" {KEY} "})
    (request,) = api.requests
    assert (request.method, request.path, request.query) == ("GET", "/v1.0/credential-check", [])
    assert request.headers["userkey"] == KEY
    assert api.connections == 1


@pytest.mark.parametrize(
    ("status", "match"),
    [
        (401, r"did not accept the API key \(HTTP 401\)"),
        (403, r"valid, but the Site-Shot account has no active subscription \(HTTP 403\)"),
        (429, r"limiting requests \(HTTP 429\)"),
        (500, r"failed \(HTTP 500\)\. The request was not retried"),
        (503, r"failed \(HTTP 503\)"),
        (302, r"redirect \(HTTP 302\), which this plugin does not follow"),
        (404, r"HTTP 404, which this plugin does not expect"),
    ],
)
def test_refused_key_check_reports_a_safe_reason(api, provider_class, status, match):
    api.respond = send(http_response(status, b"upstream details", {"Location": "https://elsewhere.example/"}))
    with pytest.raises(ToolProviderCredentialValidationError, match=match) as raised:
        validate(provider_class, {"api_key": KEY})
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert api.connections == 1


@pytest.mark.parametrize("credentials", [{}, {"api_key": None}, {"api_key": ""}, {"api_key": "two words"}])
def test_missing_or_malformed_key_never_reaches_the_network(api, provider_class, credentials):
    with pytest.raises(ToolProviderCredentialValidationError, match="API key"):
        validate(provider_class, credentials)
    assert api.connections == 0


def test_key_check_has_its_own_short_deadline(api, provider_class, monkeypatch):
    assert site_shot_api.CREDENTIAL_CHECK_TIMEOUT == 10
    monkeypatch.setattr(site_shot_api, "CREDENTIAL_CHECK_TIMEOUT", 1)
    api.respond = send(30.0)
    started = time.monotonic()
    with pytest.raises(ToolProviderCredentialValidationError, match="did not answer within 1 seconds"):
        validate(provider_class, {"api_key": KEY})
    assert 1 <= time.monotonic() - started < 1.5
    assert api.connections == 1
