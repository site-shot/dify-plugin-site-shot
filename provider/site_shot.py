from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from site_shot_api import SiteShotError, check_credentials


class SiteShotProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            check_credentials(credentials.get("api_key"))
        except SiteShotError as error:
            raise ToolProviderCredentialValidationError(str(error)) from None
