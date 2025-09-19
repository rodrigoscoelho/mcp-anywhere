from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from mcp_anywhere.container.manager import ContainerManager
from mcp_anywhere.core.mcp_manager import create_mcp_config
from mcp_anywhere.database import MCPServer


def _make_manager() -> ContainerManager:
    return ContainerManager.__new__(ContainerManager)


def test_parse_start_command_leaves_npx_commands_untouched() -> None:
    manager = _make_manager()
    server = cast(
        MCPServer,
        SimpleNamespace(
            start_command="npx @playwright/mcp@latest",
            runtime_type="npx",
        ),
    )

    result = manager._parse_start_command(server)

    assert result == ["npx", "@playwright/mcp@latest"]


def test_parse_start_command_adds_stdio_for_builtin_cli() -> None:
    manager = _make_manager()
    server = cast(
        MCPServer,
        SimpleNamespace(
            start_command="uv run mcp-anywhere serve",
            runtime_type="uvx",
        ),
    )

    result = manager._parse_start_command(server)

    assert result == ["uv", "run", "mcp-anywhere", "serve", "stdio"]


def test_parse_start_command_respects_explicit_transport() -> None:
    manager = _make_manager()
    server = cast(
        MCPServer,
        SimpleNamespace(
            start_command="uvx mcp-anywhere serve http",
            runtime_type="uvx",
        ),
    )

    result = manager._parse_start_command(server)

    assert result == ["uvx", "mcp-anywhere", "serve", "http"]


def test_parse_start_command_handles_stdio_flags() -> None:
    manager = _make_manager()
    server = cast(
        MCPServer,
        SimpleNamespace(
            start_command="uvx fastmcp serve --stdio",
            runtime_type="uvx",
        ),
    )

    result = manager._parse_start_command(server)

    assert result == ["uvx", "fastmcp", "serve", "--stdio"]


def test_create_mcp_config_sets_default_stdio_env() -> None:
    server = cast(
        MCPServer,
        SimpleNamespace(
            id="test1234",
            name="demo",
            github_url="https://example.com/repo",
            runtime_type="npx",
            start_command="npx @scope/package",
            install_command="",
            env_variables=[],
            secret_files=[],
        ),
    )

    with (
        patch.object(ContainerManager, "__init__", return_value=None),
        patch.object(ContainerManager, "_parse_start_command", return_value=["npx", "@scope/package"]),
        patch.object(ContainerManager, "get_image_tag", return_value="image:latest"),
        patch.object(ContainerManager, "_get_container_name", return_value="container-name"),
        patch.object(ContainerManager, "_get_env_vars", return_value={}),
    ):
        config = create_mcp_config(server)

    run_args = config["new"]["args"]

    assert "-e" in run_args
    env_index = run_args.index("-e")
    assert run_args[env_index + 1] == "MCP_TRANSPORT=stdio"
