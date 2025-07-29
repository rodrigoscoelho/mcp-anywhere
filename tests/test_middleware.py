from __future__ import annotations

import asyncio
import unittest

from fastmcp import Client, FastMCP

from mcp_router.middleware import ProviderFilterMiddleware


class TestProviderFilterMiddleware(unittest.TestCase):
    """Test the ProviderFilterMiddleware for hierarchical tool discovery."""

    def test_tool_filtering(self) -> None:
        """Test that middleware filters tools to show only discovery tools."""

        # 1. Create a server with both discovery and regular tools
        server = FastMCP("TestServer")
        server.add_middleware(ProviderFilterMiddleware())

        @server.tool()
        def list_providers() -> list[dict[str, str]]:
            """Discovery tool that should be visible."""
            return [{"id": "test", "name": "Test", "description": "Test server"}]

        @server.tool()
        def list_provider_tools(provider_id: str) -> list[dict[str, str]]:
            """Discovery tool that should be visible."""
            return [{"name": "test_tool", "description": "A test tool"}]

        @server.tool()
        def hidden_tool() -> str:
            """Regular tool that should be hidden by middleware."""
            return "This should not be visible"

        # 2. Create a client and test tool listing
        client = Client(server)

        async def _run() -> tuple[list[str], str]:
            async with client:
                tools = await client.list_tools()
                tool_names = [tool.name for tool in tools]
                
                # Even though hidden_tool is not listed, it should still be callable
                hidden_result = await client.call_tool("hidden_tool", {})
                
                return tool_names, hidden_result.structured_content["result"]

        # 3. Execute and verify only discovery tools are visible but all tools are callable
        visible_tools, hidden_result = asyncio.run(_run())
        self.assertEqual(set(visible_tools), {"list_providers", "list_provider_tools"})
        self.assertNotIn("hidden_tool", visible_tools)
        self.assertEqual(hidden_result, "This should not be visible")  # Tool still works!

    def test_native_prefix_handling(self) -> None:
        """Test that FastMCP handles prefixed tool calls natively without middleware intervention."""

        # 1. Create a child server with a simple tool
        child_server = FastMCP("ChildServer")

        @child_server.tool()
        def echo(text: str) -> str:
            """Return the provided text verbatim."""
            return text

        # 2. Create a parent server with middleware (middleware should NOT interfere)
        parent_server = FastMCP("ParentServer")
        parent_server.add_middleware(ProviderFilterMiddleware())

        # 3. Mount the child server with a prefix as a proxy
        prefix = "test_id"
        parent_server.mount(child_server, prefix=prefix, as_proxy=True)

        # 4. Create a client for the parent server
        client = Client(parent_server)

        async def _run() -> str:
            async with client:
                # Call the prefixed tool - FastMCP should handle this natively
                result = await client.call_tool(f"{prefix}_echo", {"text": "hello"})
                return result.structured_content["result"]

        # 5. Execute and verify FastMCP's native prefix handling works
        response = asyncio.run(_run())
        self.assertEqual(response, "hello")


if __name__ == "__main__":
    unittest.main() 