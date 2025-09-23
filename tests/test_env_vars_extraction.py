"""Test environment variable extraction and form handling."""

from types import SimpleNamespace

import pytest
from starlette.datastructures import FormData
from starlette.requests import Request

from mcp_anywhere.claude_analyzer import AsyncClaudeAnalyzer
from mcp_anywhere.web.routes import build_add_server_context, _extract_env_variables_from_form


@pytest.mark.asyncio
async def test_env_vars_extracted_from_claude():
    """Test that environment variables are properly extracted from Claude analysis."""
    analyzer = AsyncClaudeAnalyzer()

    mock_response = """
RUNTIME: npx
INSTALL: npm install -g @ahrefs/mcp
START: npx @ahrefs/mcp
NAME: ahrefs-mcp
DESCRIPTION: Ahrefs MCP server for SEO data access
ENV_VARS:
- KEY: AHREFS_API_TOKEN, DESC: API token for Ahrefs access, REQUIRED: true
- KEY: RATE_LIMIT, DESC: Rate limit for API calls, REQUIRED: false
- KEY: DEBUG_MODE, DESC: Enable debug logging, REQUIRED: false
"""

    result = analyzer._parse_claude_response(mock_response)

    assert len(result["env_variables"]) == 3

    env1 = result["env_variables"][0]
    assert env1["key"] == "AHREFS_API_TOKEN"
    assert env1["description"] == "API token for Ahrefs access"
    assert env1["required"] is True

    env2 = result["env_variables"][1]
    assert env2["key"] == "RATE_LIMIT"
    assert env2["description"] == "Rate limit for API calls"
    assert env2["required"] is False

    env3 = result["env_variables"][2]
    assert env3["key"] == "DEBUG_MODE"
    assert env3["description"] == "Enable debug logging"
    assert env3["required"] is False


def test_env_vars_form_data_structure():
    """Test that environment variables are structured correctly for form rendering."""
    analyzer = AsyncClaudeAnalyzer()

    mock_response = """
RUNTIME: npx
INSTALL: npm install -g test
START: npx test
NAME: test
DESCRIPTION: test
ENV_VARS:
- KEY: API_KEY, DESC: Required API key, REQUIRED: true
- KEY: OPTIONAL_VAR, DESC: Optional variable, REQUIRED: false
"""

    result = analyzer._parse_claude_response(mock_response)

    env_vars = result["env_variables"]
    assert isinstance(env_vars, list)

    for env_var in env_vars:
        assert "key" in env_var
        assert "description" in env_var
        assert "required" in env_var
        assert isinstance(env_var["required"], bool)


def test_env_vars_template_rendering_format():
    """Test that env vars are in the correct format for template rendering."""
    analysis_data = {
        "runtime_type": "npx",
        "name": "test-server",
        "env_variables": [
            {"key": "API_TOKEN", "description": "API access token", "required": True},
            {"key": "BASE_URL", "description": "Base URL for API", "required": False},
        ],
    }

    env_vars = analysis_data["env_variables"]
    assert len(env_vars) == 2

    required_var = next(var for var in env_vars if var["required"])
    assert required_var["key"] == "API_TOKEN"
    assert required_var["description"] == "API access token"

    optional_var = next(var for var in env_vars if not var["required"])
    assert optional_var["key"] == "BASE_URL"
    assert optional_var["description"] == "Base URL for API"


def _make_request() -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/servers/add",
        "raw_path": b"/servers/add",
        "headers": [],
        "query_string": b"",
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
        "app": SimpleNamespace(state=SimpleNamespace(transport_mode="http", mcp_manager=None)),
        "session": {},
    }
    return Request(scope)


def test_extract_env_vars_from_indexed_form():
    form_data = FormData([
        ("env_key_0", "API_KEY"),
        ("env_value_0", "secret"),
        ("env_desc_0", "API token"),
        ("env_required_0", "true"),
        ("env_key_1", "DEBUG"),
        ("env_desc_1", "Enable debug logging"),
    ])

    env_vars = _extract_env_variables_from_form(form_data)

    assert len(env_vars) == 2
    assert env_vars[0]["key"] == "API_KEY"
    assert env_vars[0]["value"] == "secret"
    assert env_vars[0]["required"] is True
    assert env_vars[1]["key"] == "DEBUG"
    assert env_vars[1]["required"] is False


def test_build_add_server_context_manual_mode_defaults():
    request = _make_request()
    context = build_add_server_context(request, mode="manual")

    assert context["config_mode"] == "manual"
    assert context["form_values"]["runtime_type"] == "uvx"
    assert "runtime_type" in context["field_guidance"]
    assert context["env_entries"] == []
