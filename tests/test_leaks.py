"""No API key, target URL, request URL or response content may reach logs, output, errors or tool messages."""

import http.client
import logging
import struct
import traceback
import zlib

import pytest
from conftest import PRODUCTION_TLS_CONTEXT, http_response, screenshot, send
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

import site_shot_api
from site_shot_api import SiteShotError

KEY = "test-key-Q7m2Zp9Kx4"
TARGET = "https://leak-check.example.com/private/path?token=t0p-s3cret"
MARKER = "UPSTREAM-MARKER-4f2c"
NEVER_SHOWN = [KEY, "userkey", "leak-check", "t0p-s3cret", MARKER]
QUIET = logging.getLogger("site_shot_tests.quiet")  # exists before the capture starts and does not propagate
QUIET.propagate = False
PNG = screenshot("png")


def assert_never_shown(*texts):
    for text in texts:
        for secret in NEVER_SHOWN:
            assert secret not in text, f"{secret!r} leaked into: {text[:300]!r}"


@pytest.fixture
def http_debug(monkeypatch):
    """Turn http.client's debug printing on for every connection that does not override it."""
    monkeypatch.setattr(http.client.HTTPConnection, "debuglevel", 1)


def test_capture_detector_sees_every_channel(logs, capfd):
    """The checks below would see a leak: debug logs, non-propagating loggers and raw output are all captured."""
    logging.getLogger("site_shot_tests.any").debug("key %s", KEY)
    QUIET.debug("url %s", TARGET)
    print(MARKER)
    out, _ = capfd.readouterr()
    joined = "\n".join(logs)
    assert KEY in joined and TARGET in joined and MARKER in out


SCENARIOS = {
    "success": send(http_response(200, PNG, {"Content-Type": "image/png", "X-Debug": MARKER})),
    "placeholder": send(http_response(403, PNG, {"Content-Type": "image/png", "X-Debug": MARKER})),
    "redirect": send(http_response(302, b"", {"Location": f"https://elsewhere.example/{MARKER}?{KEY}"})),
    "server error": send(http_response(500, f"<html>{MARKER} {KEY}</html>".encode(), {"Content-Type": "text/html"})),
    "wrong type": send(http_response(200, f"{MARKER}".encode(), {"Content-Type": "text/plain"})),
    "corrupt image": send(http_response(200, b"\x89PNG\r\n\x1a\n" + MARKER.encode(), {"Content-Type": "image/png"})),
    "cut short": send(http_response(200, PNG, {"Content-Type": "image/png"})[:-100]),
    "malformed": send(f"{MARKER} {KEY}\r\n\r\n".encode()),
    "dropped": send(),
    "timeout": send(30.0),
}


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_capture_never_shows_secrets(scenario, api, tool_class, logs, capfd, http_debug, monkeypatch):
    monkeypatch.setattr(site_shot_api, "CAPTURE_TIMEOUT", 1)
    api.respond = SCENARIOS[scenario]
    tool = tool_class.from_credentials({"api_key": KEY})
    shown = []
    try:
        for message in tool.invoke({"url": TARGET}):
            shown.append(repr(message.meta) if message.type == message.MessageType.BLOB else repr(message))
    except SiteShotError as error:
        shown.append("".join(traceback.format_exception(error)))
    assert len(api.requests) == 1 and (scenario == "success") == (len(shown) == 5)
    out, err = capfd.readouterr()
    assert_never_shown(*shown, *logs, out, err)


def test_tls_failure_never_shows_secrets(api, tool_class, logs, capfd, http_debug, monkeypatch):
    monkeypatch.setattr(site_shot_api, "_TLS_CONTEXT", PRODUCTION_TLS_CONTEXT)
    with pytest.raises(SiteShotError) as raised:
        list(tool_class.from_credentials({"api_key": KEY}).invoke({"url": TARGET}))
    out, err = capfd.readouterr()
    assert_never_shown("".join(traceback.format_exception(raised.value)), *logs, out, err)


@pytest.mark.parametrize("status", [200, 401, 403, 500])
def test_key_check_never_shows_secrets(status, api, provider_class, logs, capfd, http_debug):
    api.respond = send(http_response(status, f"{MARKER} {KEY}".encode(), {"X-Debug": MARKER}))
    shown = []
    try:
        provider_class().validate_credentials({"api_key": KEY})
    except ToolProviderCredentialValidationError as error:
        shown.append("".join(traceback.format_exception(error)))
    assert len(api.requests) == 1 and (status == 200) == (shown == [])
    out, err = capfd.readouterr()
    assert_never_shown(*shown, *logs, out, err)


def png_with_iccp_profile_name(name):
    """A valid PNG whose iCCP chunk carries ``name``; Pillow logs that name at DEBUG."""
    payload = name + b"\x00\x00" + zlib.compress(b"profile")
    chunk = b"iCCP" + payload
    png = screenshot("png")
    return png[:33] + struct.pack(">I", len(payload)) + chunk + struct.pack(">I", zlib.crc32(chunk)) + png[33:]


def jpeg_with_mpf_tag(value):
    """A valid JPEG whose MPF segment holds a TIFF tag with ``value``; Pillow logs tag values at DEBUG."""
    tiff = b"II*\x00" + struct.pack("<LH", 8, 1) + struct.pack("<HHLL", 0x9999, 2, len(value), 26)
    segment = b"MPF\x00" + tiff + struct.pack("<L", 0) + value
    jpeg = screenshot("jpeg")
    return jpeg[:2] + b"\xff\xe2" + struct.pack(">H", len(segment) + 2) + segment + jpeg[2:]


@pytest.mark.filterwarnings("ignore:Image appears to be a malformed MPO file")
@pytest.mark.parametrize("image_format", ["png", "jpeg"])
def test_image_metadata_never_reaches_logs(image_format, api, tool_class, logs, capfd):
    canary = b"UPSTREAM-IMAGE-METADATA"
    body = png_with_iccp_profile_name(canary) if image_format == "png" else jpeg_with_mpf_tag(canary + b"\x00")
    api.respond = send(http_response(200, body, {"Content-Type": site_shot_api.FORMATS[image_format].mime_type}))
    tool = tool_class.from_credentials({"api_key": KEY})
    messages = list(tool.invoke({"url": "https://example.com/", "format": image_format}))
    assert messages[0].message.blob == body  # the screenshot itself is returned unchanged
    out, err = capfd.readouterr()
    assert canary.decode() not in "\n".join([*logs, out, err])


def test_only_the_two_pillow_decoder_loggers_are_filtered(logs):
    logging.getLogger("PIL.Image").debug("other loggers still log")
    logging.getLogger("PIL.PngImagePlugin").debug("decoder record")
    logging.getLogger("PIL.TiffImagePlugin").debug("decoder record")
    assert any("other loggers still log" in record for record in logs)
    assert not any("decoder record" in record for record in logs)
    assert logging.getLogger().filters == []
    filtered = {
        name: logger.filters
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger) and logger.filters
    }
    assert filtered == {name: [site_shot_api._drop] for name in ("PIL.PngImagePlugin", "PIL.TiffImagePlugin")}


def test_decoder_loggers_keep_their_levels():
    for name in ("PIL.PngImagePlugin", "PIL.TiffImagePlugin"):
        assert logging.getLogger(name).level == logging.NOTSET and not logging.getLogger(name).disabled
