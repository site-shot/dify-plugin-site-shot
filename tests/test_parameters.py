"""Parameter validation, which runs before anything is sent."""

import math

import pytest

from site_shot_api import FORMATS, CaptureRequest, SiteShotError, parse_capture_parameters

URL = "https://example.com/"


def parse(**parameters):
    return parse_capture_parameters(parameters)


def test_defaults():
    assert parse(url=URL) == CaptureRequest(URL, FORMATS["png"], 1024, 768, full_page=False, max_height=20000)


def test_explicit_values_and_whole_number_floats():
    request = parse(url=URL, format="jpeg", width=1920.0, height=1080, full_page=True, max_height=10000.0)
    assert request == CaptureRequest(URL, FORMATS["jpeg"], 1920, 1080, full_page=True, max_height=10000)
    assert type(request.width) is int and type(request.max_height) is int


def test_empty_optional_values_take_the_defaults():
    assert parse(url=URL, format="", width=None, height="", full_page=None) == parse(url=URL)


def test_surrounding_whitespace_is_trimmed():
    assert parse(url=f"  {URL}\n").url == URL


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com:8080/path?q=1#section",
        "HTTPS://EXAMPLE.COM/",
        "https://example.com./",
        "https://sub.domain.example.co.uk/a/b?c=d&e=f",
        "https://bücher.example/",
        "https://8.8.8.8/",
        "https://[2001:4860:4860::8888]/",
    ],
)
def test_public_urls_are_accepted(url):
    assert parse(url=url).url == url


@pytest.mark.parametrize(
    ("url", "match"),
    [
        (None, "URL is required"),
        ("", "URL is required"),
        ("   ", "URL is required"),
        (123, "URL is required"),
        ("https://example.com/" + "a" * 2029, "at most 2048 characters"),
        ("https://example.com/a b", "spaces, control characters or backslashes"),
        ("https://example.com/a\nb", "spaces, control characters or backslashes"),
        ("https://example.com/\x00", "spaces, control characters or backslashes"),
        ("https://example.com/​", "spaces, control characters or backslashes"),
        ("https://example.com\\@127.0.0.1/", "spaces, control characters or backslashes"),
        ("https://127.0.0.1\\.example.com/", "spaces, control characters or backslashes"),
        ("ftp://example.com/", "start with http:// or https://"),
        ("file:///etc/passwd", "start with http:// or https://"),
        ("javascript:alert(1)", "start with http:// or https://"),
        ("example.com/path", "start with http:// or https://"),
        ("//example.com/", "start with http:// or https://"),
        ("http://user:secret@example.com/", "user name or password"),
        ("http://user@example.com/", "user name or password"),
        ("http://@example.com/", "user name or password"),
        ("http://example.com:0/", "invalid port"),
        ("http://example.com:99999/", "not a valid web address"),
        ("http://example.com:abc/", "not a valid web address"),
        ("http://[example.com]/", "not a valid web address"),
        ("http:///path", "valid host name"),
        ("http://127%2e0%2e0%2e1/", "valid host name"),
        ("http://[fe80::1%25en0]/", "valid host name"),
        ("http://127.0.0.1/", "public address"),
        ("http://10.0.0.1/", "public address"),
        ("http://172.16.5.4/", "public address"),
        ("http://192.168.1.1/", "public address"),
        ("http://169.254.169.254/latest/meta-data/", "public address"),
        ("http://100.64.0.1/", "public address"),
        ("http://0.0.0.0/", "public address"),
        ("http://224.0.0.1/", "public address"),
        ("http://255.255.255.255/", "public address"),
        ("http://[::1]/", "public address"),
        ("http://[::]/", "public address"),
        ("http://[fe80::1]/", "public address"),
        ("http://[fc00::1]/", "public address"),
        ("http://[ff02::1]/", "public address"),
        ("http://[::ffff:127.0.0.1]/", "public address"),
        ("http://[::ffff:10.0.0.1]/", "public address"),
        ("http://localhost/", "public domain name"),
        ("http://LOCALHOST./", "public domain name"),
        ("http://app.localhost/", "public domain name"),
        ("http://printer.local/", "public domain name"),
        ("http://db.internal/", "public domain name"),
        ("http://nas.home.arpa/", "public domain name"),
        ("http://intranet/", "public domain name"),
        ("http://2130706433/", "public domain name"),
        ("http://0x7f000001/", "public domain name"),
        ("http://0x7f.1/", "public domain name"),
        ("http://127.1/", "public domain name"),
        ("http://0177.0.0.1/", "public domain name"),
        ("http://1.2.3.4.5/", "public domain name"),
        ("http://.example.com/", "public domain name"),
        ("http://example..com/", "public domain name"),
    ],
)
def test_unusable_urls_are_refused(url, match):
    with pytest.raises(SiteShotError, match=match):
        parse(url=url)


@pytest.mark.parametrize("value", ["gif", "jpg", "webp", "PNG", 1, True, ["png"]])
def test_format_must_be_png_or_jpeg(value):
    with pytest.raises(SiteShotError, match="Format must be png or jpeg"):
        parse(url=URL, format=value)


@pytest.mark.parametrize(
    ("name", "value", "label"),
    [
        ("width", 99, "Width must be a whole number from 100 to 8000"),
        ("width", 8001, "Width must be"),
        ("width", 1024.5, "Width must be"),
        ("width", True, "Width must be"),
        ("width", "1024", "Width must be"),
        ("width", math.nan, "Width must be"),
        ("width", math.inf, "Width must be"),
        ("height", 99, "Height must be a whole number from 100 to 20000"),
        ("height", 20001, "Height must be"),
        ("max_height", 99, "Max height must be a whole number from 100 to 20000"),
        ("max_height", 20001, "Max height must be"),
        ("max_height", [100], "Max height must be"),
    ],
)
def test_sizes_must_be_whole_numbers_within_bounds(name, value, label):
    with pytest.raises(SiteShotError, match=label):
        parse(url=URL, **{name: value})


def test_size_bounds_are_inclusive():
    assert parse(url=URL, width=100, height=100).width == 100
    assert parse(url=URL, width=8000, height=5000).width == 8000
    assert parse(url=URL, width=2000, height=20000).height == 20000
    assert parse(url=URL, width=2000, full_page=True, max_height=20000).max_height == 20000
    assert parse(url=URL, full_page=True, max_height=100).max_height == 100


@pytest.mark.parametrize("value", ["true", "false", 1, 0, "yes"])
def test_full_page_must_be_a_boolean(value):
    with pytest.raises(SiteShotError, match="Full page must be true or false"):
        parse(url=URL, full_page=value)


def test_requests_above_the_pixel_budget_are_refused():
    with pytest.raises(SiteShotError, match="8000 x 5001 px capture is larger than the 40 megapixels"):
        parse(url=URL, width=8000, height=5001)
    with pytest.raises(SiteShotError, match="2001 x 20000 px"):
        parse(url=URL, width=2001, full_page=True)  # the default max height is 20000
    # In full-page mode the image is at most max_height tall, whatever the window height.
    assert parse(url=URL, width=8000, height=20000, full_page=True, max_height=5000).full_page
