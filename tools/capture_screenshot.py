from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from site_shot_api import capture, parse_capture_parameters


class CaptureScreenshotTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        request = parse_capture_parameters(tool_parameters)
        screenshot = capture(self.runtime.credentials.get("api_key"), request)
        image_format = screenshot.image_format
        yield self.create_blob_message(
            blob=screenshot.content,
            meta={"mime_type": image_format.mime_type, "filename": f"screenshot.{image_format.extension}"},
        )
        yield self.create_variable_message("format", image_format.name)
        yield self.create_variable_message("image_width", screenshot.width)
        yield self.create_variable_message("image_height", screenshot.height)
        yield self.create_variable_message("size_bytes", len(screenshot.content))
