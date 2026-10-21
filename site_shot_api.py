"""Site-Shot API calls for the Dify plugin: the credential check and the screenshot capture.

Both calls share one HTTPS transport (``_get``): the fixed API host, certificate
verification, no proxy, redirect or retry, one deadline for the whole exchange and a
bounded body. Every failure becomes a ``SiteShotError`` whose message never contains
the API key, a request URL or a response body.
"""

import http.client
import io
import ipaddress
import logging
import re
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import certifi
import dify_plugin  # noqa: F401  # applies the SDK's gevent patching, which the deadline relies on
import gevent
from PIL import Image

API_HOST = "api.site-shot.com"
CREDENTIAL_CHECK_PATH = "/v1.0/credential-check"
CREDENTIAL_CHECK_TIMEOUT = 10  # seconds
CAPTURE_TIMEOUT = 90  # seconds; Site-Shot spends up to 60 s rendering a page by default
MAX_IMAGE_BYTES = 30 * 1024 * 1024  # Dify refuses tool files above 30 MiB
MAX_IMAGE_PIXELS = 40_000_000  # bounds the memory needed to decode the image (about 160 MB)
MAX_URL_LENGTH = 2048
USER_AGENT = "site-shot-dify-plugin"

# name: (minimum, maximum, default), as published by the Site-Shot API
SIZE_LIMITS = {
    "width": (100, 8000, 1024),
    "height": (100, 20000, 768),
    "max_height": (100, 20000, 20000),
}
_SIZE_LABELS = {"width": "Width", "height": "Height", "max_height": "Max height"}

_TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_API_KEY = re.compile(r"[!-~]{1,256}")  # printable ASCII without spaces
_LOCAL_NAME = re.compile(r"(?:^|\.)(?:localhost|local|internal|home\.arpa)$")
_NUMERIC_LABEL = re.compile(r"0x[0-9a-f]*|[0-9]+")  # browsers read such hosts as IPv4 addresses


def _drop(_record: logging.LogRecord) -> bool:
    return False


# Pillow logs PNG chunk metadata (such as the iCCP profile name) and the TIFF tags inside JPEG
# MPF and EXIF segments at DEBUG. Those values come from the upstream image, so these two
# decoder loggers never pass a record on. No other logger is touched.
logging.getLogger("PIL.PngImagePlugin").addFilter(_drop)
logging.getLogger("PIL.TiffImagePlugin").addFilter(_drop)


class SiteShotError(Exception):
    """A failure whose message is safe to show to the user."""


@dataclass(frozen=True)
class ImageFormat:
    name: str  # the Site-Shot ``format`` value
    mime_type: str
    extension: str
    signature: bytes
    pillow_format: str


FORMATS = {
    "png": ImageFormat("png", "image/png", "png", b"\x89PNG\r\n\x1a\n", "PNG"),
    "jpeg": ImageFormat("jpeg", "image/jpeg", "jpg", b"\xff\xd8\xff", "JPEG"),
}


@dataclass(frozen=True)
class CaptureRequest:
    url: str
    image_format: ImageFormat
    width: int
    height: int
    full_page: bool
    max_height: int


@dataclass(frozen=True)
class Screenshot:
    content: bytes
    image_format: ImageFormat
    width: int
    height: int


def parse_capture_parameters(parameters: dict[str, Any]) -> CaptureRequest:
    """Validate the tool parameters before anything is sent."""
    url = _public_url(parameters.get("url"))
    format_name = _provided(parameters, "format", "png")
    if not isinstance(format_name, str) or format_name not in FORMATS:
        raise SiteShotError("Format must be png or jpeg.")
    full_page = _provided(parameters, "full_page", False)
    if not isinstance(full_page, bool):
        raise SiteShotError("Full page must be true or false.")
    width = _size(parameters, "width")
    height = _size(parameters, "height")
    max_height = _size(parameters, "max_height")
    image_height = max_height if full_page else height
    if width * image_height > MAX_IMAGE_PIXELS:
        raise SiteShotError(
            f"A {width} x {image_height} px capture is larger than the "
            f"{MAX_IMAGE_PIXELS // 1_000_000} megapixels this tool accepts. Lower the width, "
            "the height or, for a full-page capture, the max height."
        )
    return CaptureRequest(url, FORMATS[format_name], width, height, full_page, max_height)


def check_credentials(api_key: object) -> None:
    """Ask Site-Shot whether the key is usable. This never captures anything."""
    status, _ = _get(CREDENTIAL_CHECK_PATH, {"userkey": _api_key(api_key)}, CREDENTIAL_CHECK_TIMEOUT)
    if status != 200:
        raise _status_error(status, _CHECK_STATUS)


def capture(api_key: object, request: CaptureRequest) -> Screenshot:
    """Capture one screenshot and return it only if it is a complete image of the requested format."""
    query: dict[str, str | int] = {
        "userkey": _api_key(api_key),
        "url": request.url,
        "format": request.image_format.name,
        "response_type": "image",
        "width": request.width,
        "height": request.height,
        "full_size": int(request.full_page),
    }
    if request.full_page:
        query["max_height"] = request.max_height
    status, body = _get("/?" + urlencode(query), {}, CAPTURE_TIMEOUT, request.image_format.mime_type)
    if status != 200:
        raise _status_error(status, _CAPTURE_STATUS)
    width, height = _decoded_size(body, request.image_format)
    return Screenshot(body, request.image_format, width, height)


def _get(target: str, headers: dict[str, str], timeout: int, body_type: str | None = None) -> tuple[int, bytes]:
    """Send one GET to the API host and return the status and, for HTTP 200 with ``body_type``, the body.

    One deadline covers DNS, connect, TLS, request and response. http.client reads no proxy
    settings, follows no redirects, retries nothing and logs nothing.
    """
    connection = http.client.HTTPSConnection(API_HOST, context=_TLS_CONTEXT)
    connection.set_debuglevel(0)  # a debuglevel set on the class would print the request line with the key
    deadline = gevent.Timeout(timeout)
    deadline.start()
    try:
        connection.request("GET", target, headers={"User-Agent": USER_AGENT, **headers})
        response = connection.getresponse()
        if response.status != 200 or body_type is None:
            return response.status, b""
        return response.status, _read_body(response, body_type)
    except gevent.Timeout as error:
        if error is not deadline:
            raise
        raise SiteShotError(
            f"Site-Shot did not answer within {timeout} seconds. The request was not retried."
        ) from None
    except ssl.SSLCertVerificationError:
        raise SiteShotError("The TLS certificate of the Site-Shot API could not be verified.") from None
    except http.client.RemoteDisconnected:
        raise SiteShotError("Site-Shot closed the connection without answering. The request was not retried.") from None
    except http.client.IncompleteRead:
        raise SiteShotError("The connection to Site-Shot closed before the answer was complete.") from None
    except http.client.HTTPException:
        raise SiteShotError("Site-Shot sent a malformed HTTP answer.") from None
    except OSError:
        raise SiteShotError("Could not connect to the Site-Shot API.") from None
    finally:
        deadline.close()
        connection.close()


def _read_body(response: http.client.HTTPResponse, body_type: str) -> bytes:
    content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
    if content_type != body_type:
        raise SiteShotError(f"Site-Shot answered with another content type instead of {body_type}.")
    if response.length is not None and response.length > MAX_IMAGE_BYTES:
        raise _too_large()
    chunks = []
    size = 0
    while chunk := response.read1(64 * 1024):
        size += len(chunk)
        if size > MAX_IMAGE_BYTES:
            raise _too_large()
        chunks.append(chunk)
    if response.length:  # the connection closed before the declared Content-Length arrived
        raise http.client.IncompleteRead(b"")
    return b"".join(chunks)


def _decoded_size(body: bytes, image_format: ImageFormat) -> tuple[int, int]:
    """Return the size of the image after checking its signature and decoding all of it."""
    label = image_format.name.upper()
    if not body.startswith(image_format.signature):
        raise SiteShotError(f"Site-Shot answered with something other than a {label} image.")
    try:
        with Image.open(io.BytesIO(body), formats=[image_format.pillow_format]) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise _too_many_pixels()
            if image_format.name == "png":
                image.verify()  # every chunk checksum through the closing IEND chunk
        with Image.open(io.BytesIO(body), formats=[image_format.pillow_format]) as image:
            image.load()  # decodes every pixel; a truncated image raises OSError
    except Image.DecompressionBombError:
        raise _too_many_pixels() from None
    except (OSError, SyntaxError, ValueError, EOFError):
        raise SiteShotError(f"Site-Shot answered with a corrupt or incomplete {label} image.") from None
    return width, height


def _too_many_pixels() -> SiteShotError:
    return SiteShotError(
        f"The screenshot is larger than {MAX_IMAGE_PIXELS // 1_000_000} megapixels, the most this tool "
        "decodes. The capture may still count toward your plan."
    )


def _too_large() -> SiteShotError:
    return SiteShotError(
        f"The screenshot is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MiB, the most Dify accepts. "
        "Use JPEG or a lower max height. The capture may still count toward your plan."
    )


def _api_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SiteShotError("The Site-Shot API key is missing. Add it in the plugin's authorization settings.")
    key = value.strip()
    if not _API_KEY.fullmatch(key):
        raise SiteShotError("The Site-Shot API key contains characters that an API key cannot have.")
    return key


def _provided(parameters: dict[str, Any], name: str, default: object) -> object:
    value = parameters.get(name)
    return default if value is None or value == "" else value


def _size(parameters: dict[str, Any], name: str) -> int:
    low, high, default = SIZE_LIMITS[name]
    value = _provided(parameters, name, default)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or not low <= value <= high:  # True and False fall below every minimum
        raise SiteShotError(f"{_SIZE_LABELS[name]} must be a whole number from {low} to {high}.")
    return value


def _public_url(value: object) -> str:
    """Accept only an absolute http(s) URL whose host is not obviously private.

    This is input hygiene, not a network guarantee: a public name may still resolve to a
    private address or redirect there. Site-Shot applies its own network policy when rendering.
    """
    if not isinstance(value, str) or not value.strip():
        raise SiteShotError("URL is required.")
    url = value.strip()
    if len(url) > MAX_URL_LENGTH:
        raise SiteShotError(f"URL must be at most {MAX_URL_LENGTH} characters.")
    if any(char.isspace() or not char.isprintable() or char == "\\" for char in url):
        raise SiteShotError("URL must not contain spaces, control characters or backslashes.")
    try:
        parts = urlsplit(url)
        valid_port = parts.port != 0
    except ValueError:  # malformed brackets, or a port that is not a number from 0 to 65535
        raise SiteShotError("URL is not a valid web address.") from None
    if parts.scheme not in ("http", "https"):
        raise SiteShotError("URL must start with http:// or https://.")
    if "@" in parts.netloc:
        raise SiteShotError("URL must not contain a user name or password.")
    if not valid_port:
        raise SiteShotError("URL has an invalid port.")
    host = (parts.hostname or "").removesuffix(".")
    if not host or "%" in host:
        raise SiteShotError("URL must include a valid host name.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global or address.is_multicast:  # also covers IPv4-mapped IPv6 addresses
            raise SiteShotError("URL must point to a public address, not a private, local or reserved one.")
        return url
    labels = host.split(".")
    if "" in labels or len(labels) < 2 or _NUMERIC_LABEL.fullmatch(labels[-1]) or _LOCAL_NAME.search(host):
        raise SiteShotError("URL host must be a public domain name such as example.com, or a public IP address.")
    return url


_CHECK_STATUS = {
    401: "Site-Shot did not accept the API key (HTTP 401).",
    403: "The API key is valid, but the Site-Shot account has no active subscription (HTTP 403).",
    429: "Site-Shot is limiting requests (HTTP 429). Try again later.",
}

_CAPTURE_STATUS = {
    400: "Site-Shot rejected the capture request (HTTP 400). Check the URL and the size settings.",
    401: "Site-Shot did not accept the API key (HTTP 401). Check the key in the plugin's authorization settings.",
    403: (
        "Site-Shot refused the capture (HTTP 403): the plan may be inactive or out of captures, "
        "no capture worker may be free, or the address may not be allowed."
    ),
    404: (
        "Site-Shot could not capture the page (HTTP 404): it may not have loaded, or its host name "
        "may not resolve. The placeholder image sent with this answer was discarded."
    ),
    429: (
        "Site-Shot refused the capture (HTTP 429): the plan's limit of captures at the same time "
        "was reached. The capture was not retried."
    ),
}


def _status_error(status: int, messages: dict[int, str]) -> SiteShotError:
    if status in messages:
        return SiteShotError(messages[status])
    if 300 <= status < 400:
        return SiteShotError(f"Site-Shot answered with a redirect (HTTP {status}), which this plugin does not follow.")
    if status >= 500:
        return SiteShotError(
            f"Site-Shot or a gateway in front of it failed (HTTP {status}). The request was not retried."
        )
    return SiteShotError(f"Site-Shot answered with HTTP {status}, which this plugin does not expect.")
