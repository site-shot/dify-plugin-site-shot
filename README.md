# Site-Shot for Dify

A Dify tool plugin with one tool, **Capture screenshot**: it turns a public web page URL into a PNG or JPEG file. Dify returns the file in the tool's `files` output, so a workflow can pass it to a vision-capable LLM node as an image.

The plugin itself is free. The captures run on the [Site-Shot API](https://www.site-shot.com/), which needs a Site-Shot account with a paid plan or included capture quota; every capture counts toward that plan.

## Status

| Stage | Status |
|---|---|
| Implementation | Done in this folder: one provider, one tool, built on `dify_plugin` 0.10.2 |
| Offline acceptance | SDK-level stand-in tests, lint and type checks passed. The October 21 archive and a documentation-only October 26 archive passed local offline Marketplace validation without blocking findings; warnings remain. Any later distribution archive must be validated on its exact bytes. |
| Native UI acceptance | Installed in isolated Dify Community Edition 1.17.1. On October 22, 2026, a real provider credential was accepted and two native tool runs returned one PNG viewport file and one JPEG full-page file. Both images were decoded and visually inspected; the temporary test environment was removed and the demo subscription restored |
| Live API acceptance | Two Site-Shot captures succeeded through the installed native Dify tool on October 22, 2026. On October 26, one additional PNG capture succeeded through the official remote-debug path in isolated Dify Community Edition 1.17.1; its 1024×768 output was decoded and visually inspected, the demo subscription restored and the temporary environment removed. Dify Cloud, a downstream vision-LLM workflow and stitching of a genuinely long page remain unverified |
| Marketplace publication | No Marketplace listing is confirmed. Isolated Dify Community Edition installed-package and remote-debug tests passed; Dify Cloud and downstream vision-LLM use remain untested. A matching final source and validated archive, publisher acceptance of the current Plugin Development Guidelines, a submission PR and Marketplace review are separate gates; a local validator pass or PR is not acceptance. |
| Customers attributable to this plugin | None established |

Source repository: [https://github.com/site-shot/dify-plugin-site-shot](https://github.com/site-shot/dify-plugin-site-shot).
Support: [GitHub issues](https://github.com/site-shot/dify-plugin-site-shot/issues) or [support@site-shot.com](mailto:support@site-shot.com).

## Install

The plugin is not on the Dify Marketplace. From the directory containing a checkout named `dify-plugin-site-shot`, build a package with the [Dify plugin CLI](https://docs.dify.ai/en/develop-plugin/getting-started/cli):

```bash
dify plugin package ./dify-plugin-site-shot
```

Then upload the `.difypkg` in Dify under **Plugins → Install plugin → Local package file**. The package is not signed, so a Dify instance that enforces plugin signature verification may refuse it. Do not turn verification off on a shared instance to install it.

## Authorize

In Dify, open the Site-Shot tool provider, choose **Authorize** and paste the Site-Shot API key. Dify stores it as a secret provider credential; it is never a tool parameter, so an LLM never sees it.

Saving the key runs one check, `GET https://api.site-shot.com/v1.0/credential-check` with the key in the `userkey` header. It sends no page address and captures nothing.

| Result | Meaning |
|---|---|
| Saved | Site-Shot accepted the key |
| HTTP 401 | Site-Shot does not know the key |
| HTTP 403 | The key is valid, but the account has no active subscription |
| Other | Redirects, rate limits and server errors are reported with their HTTP status; timeouts and connection failures have none. Nothing is retried |

## Use: Capture screenshot

| Parameter | Filled by | Default | Allowed |
|---|---|---|---|
| URL | the LLM, or a workflow variable | — | a public `http://` or `https://` address, at most 2048 characters |
| Format | node setting | PNG | PNG or JPEG |
| Width | node setting | 1024 | 100–8000 px, browser window width |
| Height | node setting | 768 | 100–20000 px, browser window height |
| Full page | node setting | off | capture the whole scrolling page |
| Max height | node setting | 20000 | 100–20000 px; full page only, longer pages are cut off |

Width × height, or width × max height for a full-page capture, may be at most 40 megapixels; larger requests are refused before anything is sent.

The tool returns:

- `files`: the screenshot as `screenshot.png` (`image/png`) or `screenshot.jpg` (`image/jpeg`), the bytes Site-Shot rendered. The plugin never returns a link that Dify would fetch again.
- Variables `format`, `image_width`, `image_height` and `size_bytes`, described in the tool's output schema. `text` and `json` stay empty.

### Workflow recipe

1. **Start**: add a text input `page_url`.
2. **Site-Shot → Capture screenshot**: set **URL** to the Start node's `page_url`; choose the format and size in the node settings.
3. **LLM**: pick a vision-capable model, turn on **Vision** and select the tool node's `files` output as the image. Prompt, for example: "Describe the layout of this page and list any visible errors."
4. **End**: output the LLM's text.

Two installed-package native Dify tool calls with a real API key returned image files on October 22, 2026; one official remote-debug native call returned a PNG in isolated Dify Community Edition 1.17.1 on October 26. This downstream vision-LLM recipe and Dify Cloud have not been tested.

## When a capture fails

The tool raises an error and returns no file and no metadata. Nothing is retried.

| Answer from Site-Shot | Reported as |
|---|---|
| 3xx | a redirect, which the plugin does not follow |
| 400 | the request was rejected; check the URL and size settings |
| 401 | the API key was not accepted |
| 403 | the plan may be inactive or out of captures, no capture worker may be free, or the address may not be allowed |
| 404 | the page could not be captured; the placeholder image Site-Shot sends with failures is discarded |
| 429 | the plan's limit of simultaneous captures was reached |
| 5xx | Site-Shot or a gateway in front of it failed |
| 200 that is not a complete image of the requested format | wrong content type, wrong signature, a corrupt or cut-off image, or an image above 30 MiB or 40 megapixels |
| no complete answer within 90 seconds | a timeout |

Captures refused for size may still count toward your plan.

## Security and limits

- **One endpoint.** Requests go only to `https://api.site-shot.com/`, with certificate and host name verification against the [certifi](https://pypi.org/project/certifi/) CA bundle. Proxy environment variables are ignored, redirects are not followed and nothing is retried.
- **Bounded time and size.** One deadline covers DNS, connection, TLS, request and response: 90 seconds for a capture, 10 seconds for a key check. Bodies are read only for HTTP 200 with the expected content type, and only up to 30 MiB, the most Dify accepts for a tool file. Other answers are closed unread.
- **Image checks.** The first bytes must be the PNG or JPEG signature of the requested format. The image is then decoded in full with Pillow, after a 40-megapixel limit that keeps decoding to about 160 MB of memory. For PNG every chunk checksum through the closing `IEND` chunk is verified too. JPEG decoding rejects cut-off files but tolerates some damage inside a complete file, as JPEG decoders do.
- **URL checks are input hygiene, not a network guarantee.** The tool refuses non-HTTP(S) addresses, user names and passwords in URLs, IP addresses that are not public, local names (`localhost`, `.local`, `.internal`, `.home.arpa`, single-label hosts) and numeric host forms that browsers read as IP addresses. It does not resolve DNS or follow the page's redirects: a public name can still lead to a private address. Site-Shot renders pages with its own network policy.
- **The API key and response content.** The key check sends the key in the `userkey` header. Captures send it as the `userkey` query parameter, as the Site-Shot API requires. The plugin never logs, prints or returns the key or a request URL. The only response content it passes on is the validated screenshot, as the tool's file output; error messages are fixed texts that quote no response headers or bodies and give the HTTP status when there is one. The transport is Python's `http.client`, which does not log, with its debug output pinned off for every connection. Pillow writes image metadata to its debug log (PNG chunks, and the TIFF tags inside JPEG segments), so the plugin drops every record of those two Pillow loggers; no other logger, level or handler is changed.
- **Memory.** The manifest declares 512 MiB. Decoding a 40-megapixel image took about 225 MiB of peak memory in local measurement, including the SDK runtime; the response body adds at most 30 MiB.

See [PRIVACY.md](PRIVACY.md) for what is sent where.

## Development

Run every command from this folder with a Python 3.12 virtual environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest
.venv/bin/ruff check --no-cache . && .venv/bin/ruff format --check --no-cache .
.venv/bin/mypy --strict --ignore-missing-imports --allow-subclassing-any --cache-dir /dev/null site_shot_api.py provider/site_shot.py tools/capture_screenshot.py main.py
```

The tests load the plugin through the SDK's own registration, invoke the real provider and tool classes and talk to a local TLS server that stands in for `api.site-shot.com`. Real DNS and every non-loopback connection fail the test run, and all images are synthetic.

`.difyignore` keeps tests, development files, caches, logs, `.env*` files and `.git` out of the package.

## Icon

`_assets/icon.svg` is the Safari pinned-tab mark of the Site-Shot website (`static/safari-pinned-tab.svg` in the Site-Shot web-site repository, added in commit `905428ac`), filled with the `mask-icon` color `#5bbad5` that the website declares for it.
