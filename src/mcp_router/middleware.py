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
    
    Note: This middleware does NOT handle tool call routing. FastMCP's native
    mount() functionality handles prefixed tool calls automatically when the
    mounted servers are properly configured and reachable.
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
