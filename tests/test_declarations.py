"""The manifest and the provider and tool declarations, as the dify_plugin SDK parses and loads them."""

from dify_plugin.entities.tool import ToolParameter

import site_shot_api

FORM = ToolParameter.ToolParameterForm


def test_sdk_loads_one_provider_with_one_tool(registration, provider_class, tool_class):
    assert registration.configuration.name == "site_shot"
    assert [plugin for plugin in registration.configuration.plugins.tools] == ["provider/site_shot.yaml"]
    _, loaded_provider, tools = registration.tools_mapping["site_shot"]
    assert loaded_provider is provider_class and provider_class.__name__ == "SiteShotProvider"
    assert list(tools) == ["capture_screenshot"] and tool_class.__name__ == "CaptureScreenshotTool"
    assert [asset.filename for asset in registration.files] == ["icon.svg"]


def test_api_key_is_a_provider_secret_and_never_a_tool_parameter(registration):
    provider, _, tools = registration.tools_mapping["site_shot"]
    (credential,) = provider.credentials_schema
    assert (credential.name, credential.type.value, credential.required) == ("api_key", "secret-input", True)
    tool, _ = tools["capture_screenshot"]
    assert not [parameter.name for parameter in tool.parameters if "key" in parameter.name]


def test_parameters_match_the_validation_in_code(registration):
    tool, _ = registration.tools_mapping["site_shot"][2]["capture_screenshot"]
    parameters = {parameter.name: parameter for parameter in tool.parameters}
    assert list(parameters) == ["url", "format", "width", "height", "full_page", "max_height"]
    assert (parameters["url"].form, parameters["url"].required) == (FORM.LLM, True)
    assert {name for name, parameter in parameters.items() if parameter.form == FORM.FORM} == set(parameters) - {"url"}
    for name, (low, high, default) in site_shot_api.SIZE_LIMITS.items():
        assert (parameters[name].min, parameters[name].max, parameters[name].default) == (low, high, default)
    assert [option.value for option in parameters["format"].options] == list(site_shot_api.FORMATS)
    assert (parameters["format"].default, parameters["full_page"].default) == ("png", False)


def test_output_schema_lists_the_variables_the_tool_returns(registration):
    tool, _ = registration.tools_mapping["site_shot"][2]["capture_screenshot"]
    assert tool.output_schema["type"] == "object"
    assert list(tool.output_schema["properties"]) == ["format", "image_width", "image_height", "size_bytes"]


def test_production_transport_verifies_certificates_and_host_names():
    context = site_shot_api._TLS_CONTEXT
    assert context.verify_mode.name == "CERT_REQUIRED" and context.check_hostname
    assert context.cert_store_stats()["x509_ca"] > 100  # the certifi bundle, not an empty store
    assert site_shot_api.API_HOST == "api.site-shot.com"
