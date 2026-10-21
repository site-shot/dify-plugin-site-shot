"""The capture tool through the SDK: request shape, native file output and every refusal path."""

import re

import pytest
from conftest import chunked, http_response, screenshot, send
from dify_plugin.entities.tool import ToolInvokeMessage

import site_shot_api
from site_shot_api import SiteShotError

KEY = "test-key-Q7m2Zp9Kx4"
URL = "https://example.com/pricing?plan=team"
BLOB = ToolInvokeMessage.MessageType.BLOB
VARIABLE = ToolInvokeMessage.MessageType.VARIABLE


def run(tool_class, parameters, credentials=None):
    """Invoke the tool like the SDK executor; return the messages produced and the error, if any."""
    tool = tool_class.from_credentials({"api_key": KEY} if credentials is None else credentials)
    messages = []
    try:
        for message in tool.invoke(parameters):
            messages.append(message)
    except SiteShotError as error:
        return messages, error
    return messages, None


def refused(tool_class, parameters, match, credentials=None):
    messages, error = run(tool_class, parameters, credentials)
    assert messages == [], "no file or metadata may be returned with an error"
    assert error is not None and re.search(match, str(error)), error
    return error


def image_answer(body, content_type="image/png"):
    return send(http_response(200, body, {"Content-Type": content_type}))


def test_png_capture_returns_a_native_file_and_metadata(api, tool_class):
    png = screenshot("png")
    api.respond = image_answer(png)
    messages, error = run(tool_class, {"url": URL})
    assert error is None
    blob, *variables = messages
    assert blob.type == BLOB and blob.message.blob == png
    assert blob.meta == {"mime_type": "image/png", "filename": "screenshot.png"}
    assert [(m.type, m.message.variable_name, m.message.variable_value) for m in variables] == [
        (VARIABLE, "format", "png"),
        (VARIABLE, "image_width", 320),
        (VARIABLE, "image_height", 200),
        (VARIABLE, "size_bytes", len(png)),
    ]
    (request,) = api.requests
    assert (api.connections, request.method, request.path) == (1, "GET", "/")
    assert request.query == [
        ("userkey", KEY),
        ("url", URL),
        ("format", "png"),
        ("response_type", "image"),
        ("width", "1024"),
        ("height", "768"),
        ("full_size", "0"),
    ]
    assert "userkey" not in request.headers  # the capture contract takes the key in the query only
    assert request.headers["host"] == "api.site-shot.com"


def test_full_page_jpeg_capture_sends_max_height_and_returns_jpeg(api, tool_class):
    jpeg = screenshot("jpeg", 640, 1500)
    api.respond = image_answer(jpeg, "image/jpeg; charset=binary")
    parameters = {"url": URL, "format": "jpeg", "width": 640.0, "height": 480, "full_page": True, "max_height": 3000}
    messages, error = run(tool_class, parameters)
    assert error is None
    assert messages[0].message.blob == jpeg
    assert messages[0].meta == {"mime_type": "image/jpeg", "filename": "screenshot.jpg"}
    assert [m.message.variable_value for m in messages[1:]] == ["jpeg", 640, 1500, len(jpeg)]
    assert dict(api.requests[0].query) == {
        "userkey": KEY,
        "url": URL,
        "format": "jpeg",
        "response_type": "image",
        "width": "640",
        "height": "480",
        "full_size": "1",
        "max_height": "3000",
    }


@pytest.mark.parametrize("credentials", [{}, {"api_key": None}, {"api_key": ""}, {"api_key": "   "}])
def test_missing_key_is_refused_before_any_request(api, tool_class, credentials):
    refused(tool_class, {"url": URL}, "API key is missing", credentials)
    assert api.connections == 0


@pytest.mark.parametrize("key", ["two words", "key\r\nX-Injected: 1", "ключ", "k" * 257])
def test_malformed_key_is_refused_before_any_request(api, tool_class, key):
    refused(tool_class, {"url": URL}, "characters that an API key cannot have", {"api_key": key})
    assert api.connections == 0


@pytest.mark.parametrize(
    ("parameters", "match"),
    [
        ({}, "URL is required"),
        ({"url": "http://127.0.0.1/"}, "public address"),
        ({"url": URL, "format": "webp"}, "png or jpeg"),
        ({"url": URL, "width": 8001}, "Width must be"),
        ({"url": URL, "width": 8000, "height": 20000}, "megapixels"),
    ],
)
def test_invalid_parameters_are_refused_before_any_request(api, tool_class, parameters, match):
    refused(tool_class, parameters, match)
    assert api.connections == 0


@pytest.mark.parametrize(
    ("status", "match"),
    [
        (301, r"redirect \(HTTP 301\), which this plugin does not follow"),
        (302, r"redirect \(HTTP 302\)"),
        (303, r"redirect \(HTTP 303\)"),
        (307, r"redirect \(HTTP 307\)"),
        (308, r"redirect \(HTTP 308\)"),
        (400, r"rejected the capture request \(HTTP 400\)"),
        (401, r"did not accept the API key \(HTTP 401\)"),
        (403, r"refused the capture \(HTTP 403\)"),
        (404, r"\(HTTP 404\).*placeholder image sent with this answer was discarded"),
        (429, r"\(HTTP 429\).*not retried"),
        (500, r"failed \(HTTP 500\)\. The request was not retried"),
        (502, r"failed \(HTTP 502\)"),
        (503, r"failed \(HTTP 503\)"),
        (504, r"failed \(HTTP 504\)"),
        (418, r"HTTP 418, which this plugin does not expect"),
    ],
)
def test_failure_status_discards_the_placeholder_and_is_not_retried(api, tool_class, status, match):
    placeholder = screenshot("png")  # Site-Shot sends a placeholder image with failures in image mode
    headers = {"Content-Type": "image/png", "Location": "https://elsewhere.example/next.png"}
    api.respond = send(http_response(status, placeholder, headers))
    refused(tool_class, {"url": URL}, match)
    assert api.connections == 1 and len(api.requests) == 1


@pytest.mark.parametrize(
    ("answer", "match"),
    [
        (http_response(200, b"<html>busy</html>", {"Content-Type": "text/html"}), "another content type"),
        (http_response(200, screenshot("png"), {"Content-Type": ""}), "another content type"),
        (http_response(200, screenshot("jpeg"), {"Content-Type": "image/jpeg"}), "another content type"),
        (http_response(200, screenshot("jpeg"), {"Content-Type": "image/png"}), "other than a PNG image"),
        (http_response(200, b"", {"Content-Type": "image/png"}), "other than a PNG image"),
        (http_response(200, b"\x89PNG\r\n\x1a\nnot really", {"Content-Type": "image/png"}), "corrupt or incomplete"),
    ],
)
def test_answers_that_are_not_the_requested_image_are_refused(api, tool_class, answer, match):
    api.respond = send(answer)
    refused(tool_class, {"url": URL}, match)


def test_png_that_is_cut_short_is_refused(api, tool_class):
    png = screenshot("png", 800, 600)
    api.respond = image_answer(png[: len(png) // 2])
    refused(tool_class, {"url": URL}, "corrupt or incomplete PNG")


def test_png_without_its_closing_chunk_is_refused(api, tool_class):
    png = screenshot("png")
    assert png.endswith(b"IEND\xaeB`\x82")
    api.respond = image_answer(png[:-12])  # the pixels decode, but the file is not complete
    refused(tool_class, {"url": URL}, "corrupt or incomplete PNG")


def test_png_with_a_damaged_chunk_checksum_is_refused(api, tool_class):
    png = bytearray(screenshot("png"))
    data_start = png.index(b"IDAT") + 4
    png[data_start + 10] ^= 0xFF
    api.respond = image_answer(bytes(png))
    refused(tool_class, {"url": URL}, "corrupt or incomplete PNG")


def test_jpeg_that_is_cut_short_is_refused(api, tool_class):
    jpeg = screenshot("jpeg", 800, 600)
    api.respond = image_answer(jpeg[: len(jpeg) // 2], "image/jpeg")
    refused(tool_class, {"url": URL, "format": "jpeg"}, "corrupt or incomplete JPEG")


def test_answer_shorter_than_its_content_length_is_refused(api, tool_class):
    png = screenshot("png")
    head = http_response(200, png, {"Content-Type": "image/png"})[: -len(png)]
    api.respond = send(head + png[:100])
    refused(tool_class, {"url": URL}, "closed before the answer was complete")


def test_declared_size_above_the_limit_is_refused_before_reading(api, tool_class):
    limit = site_shot_api.MAX_IMAGE_BYTES
    api.respond = send(http_response(200, b"", {"Content-Type": "image/png", "Content-Length": str(limit + 1)}))
    refused(tool_class, {"url": URL}, "larger than 30 MiB")


def test_chunked_answer_above_the_limit_is_refused(api, tool_class, monkeypatch):
    png = screenshot("png")
    monkeypatch.setattr(site_shot_api, "MAX_IMAGE_BYTES", len(png) - 1)
    headers = {"Content-Type": "image/png", "Content-Length": "", "Transfer-Encoding": "chunked"}
    api.respond = send(http_response(200, b"", headers) + chunked(png, 1000))
    refused(tool_class, {"url": URL}, "MiB, the most Dify accepts")


def test_answer_of_exactly_the_limit_is_accepted(api, tool_class, monkeypatch):
    png = screenshot("png")
    monkeypatch.setattr(site_shot_api, "MAX_IMAGE_BYTES", len(png))
    api.respond = image_answer(png)
    messages, error = run(tool_class, {"url": URL})
    assert error is None and messages[0].message.blob == png


def test_chunked_and_unsized_answers_are_read_completely(api, tool_class):
    png = screenshot("png", 900, 700)
    headers = {"Content-Type": "image/png", "Content-Length": "", "Transfer-Encoding": "chunked"}
    api.respond = send(http_response(200, b"", headers) + chunked(png, 1000))
    assert run(tool_class, {"url": URL})[0][0].message.blob == png
    api.respond = send(http_response(200, png, {"Content-Type": "image/png", "Content-Length": ""}))
    assert run(tool_class, {"url": URL})[0][0].message.blob == png


def test_unsized_answer_cut_short_is_refused_by_decoding(api, tool_class):
    png = screenshot("png", 800, 600)
    api.respond = send(http_response(200, png[: len(png) // 2], {"Content-Type": "image/png", "Content-Length": ""}))
    refused(tool_class, {"url": URL}, "corrupt or incomplete PNG")


def test_image_with_more_pixels_than_the_limit_is_refused_before_decoding(api, tool_class, monkeypatch):
    monkeypatch.setattr(site_shot_api, "MAX_IMAGE_PIXELS", 320 * 200 - 1)
    api.respond = image_answer(screenshot("png"))
    refused(tool_class, {"url": URL, "width": 100, "height": 100}, "megapixels, the most this tool decodes")


def test_malformed_http_answer_is_refused(api, tool_class):
    api.respond = send(b"NOT HTTP AT ALL\r\n\r\n")
    refused(tool_class, {"url": URL}, "malformed HTTP answer")


def test_connection_closed_without_an_answer_is_not_retried(api, tool_class):
    api.respond = send()
    refused(tool_class, {"url": URL}, "closed the connection without answering")
    assert api.connections == 1
