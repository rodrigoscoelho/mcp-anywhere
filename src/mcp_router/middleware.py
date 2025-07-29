"""Middleware for hierarchical tool discovery in MCP Router"""

from typing import Dict, Any, Callable, Awaitable
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

    async def on_message(
        self,
        ctx: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[Any]],
    ) -> Any:
        """
        Intercept all JSON-RPC messages and filter tool listings.

        Based on FastMCP middleware documentation, we use on_message to intercept
        all messages and filter responses without breaking tool registration.
        """
        # Process the request normally
        result = await call_next(ctx)

        # Only filter the response for tools/list method
        if ctx.method == "tools/list":
            logger.info("Intercepting tools/list response to filter discovery tools")

            # For tools/list, the result is typically a list of tool objects
            if isinstance(result, list):
                all_tools = result

                # Filter to only show discovery tools
                discovery_tools = [
                    tool
                    for tool in all_tools
                    if hasattr(tool, "name")
                    and tool.name in ["list_providers", "list_provider_tools"]
                ]

                logger.info(
                    f"Filtered tools list from {len(all_tools)} to {len(discovery_tools)} discovery tools"
                )

                # Return filtered list
                return discovery_tools
            elif isinstance(result, dict) and "tools" in result:
                # Alternative response format
                all_tools = result["tools"]

                # Filter to only show discovery tools
                discovery_tools = [
                    tool
                    for tool in all_tools
                    if (
                        isinstance(tool, dict)
                        and tool.get("name") in ["list_providers", "list_provider_tools"]
                    )
                    or (
                        hasattr(tool, "name")
                        and tool.name in ["list_providers", "list_provider_tools"]
                    )
                ]

                logger.info(
                    f"Filtered tools list from {len(all_tools)} to {len(discovery_tools)} discovery tools"
                )

                # Return modified result
                result["tools"] = discovery_tools
                return result

        return result
