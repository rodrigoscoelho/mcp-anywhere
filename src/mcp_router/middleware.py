"""Middleware for hierarchical tool discovery in MCP Router"""

from typing import Dict, Any, Callable, Awaitable, List
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp_router.logging_config import get_logger

logger = get_logger(__name__)


class ProviderFilterMiddleware(Middleware):
    """
    Middleware that implements hierarchical tool discovery.

    - list_tools: shows only discovery tools (list_providers, list_provider_tools)
    - list_provider_tools: shows provider's tools with correct prefixes
    """

    async def on_list_tools(
        self,
        ctx: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[List[Any]]],
    ) -> List[Any]:
        """
        Filter tool listings to show only discovery tools.

        This middleware intercepts the list of tool *objects* before they are
        serialized and filters them, returning a list of objects that
        FastMCP can then correctly process.
        """
        logger.info("Filtering tools list to show only discovery tools")

        # Get the full list of tool objects from the router
        all_tools = await call_next(ctx)

        # Filter to only show discovery tools by checking the tool object's name
        discovery_tools = [
            tool
            for tool in all_tools
            if hasattr(tool, "name") and tool.name in ["list_providers", "list_provider_tools"]
        ]

        logger.info(f"Filtered tools list to {len(discovery_tools)} discovery tools")
        return discovery_tools

    async def on_call_tool(
        self,
        ctx: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """
        Handle tool calls, logging for debugging purposes.

        Since tools are already mounted with their full prefixed names
        (e.g., "d2107d3d_execute_code"), we don't need to modify the
        tool names - FastMCP's routing will handle them correctly.
        """
        # Just log the tool call for debugging
        # The actual tool name is embedded in the JSON-RPC request,
        # but we don't need to modify it since FastMCP handles routing
        logger.debug("Processing tool call through middleware")

        # Pass through to the next handler without modification
        return await call_next(ctx)
