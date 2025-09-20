"""FastMCP middleware to filter tools based on database enable/disable state.

This integrates with FastMCP's middleware lifecycle (e.g., on_list_tools)
so filtering happens at the correct protocol layer, before tools are exposed.

References:
- Filtering is performed by overriding the FastMCP middleware hook that lists tools,
  similar to patterns used in related projects [server.py][1], [middleware.py][2].

"""

from collections.abc import Mapping, Sequence
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from sqlalchemy import select

from mcp_anywhere.database import MCPServerTool, get_async_session
from mcp_anywhere.logging_config import get_logger

logger = get_logger(__name__)


class ToolFilterMiddleware(Middleware):
    """FastMCP middleware that filters disabled tools during tools/list.

    Hooks into FastMCP's lifecycle so tools are filtered before exposure.
    """

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        """Called by FastMCP before returning the available tools list.

        Args:
            context: Middleware context from FastMCP
            call_next: Function to continue middleware chain

        Returns:
            list[Any]: Filtered list with disabled tools removed
        """
        # Get the tools from the next middleware in the chain
        tools = await call_next(context)

        try:
            disabled_tools = await self._get_disabled_tools_async()
        except Exception as exc:  # Do not fail tool listing on DB errors
            logger.exception(f"Tool filtering skipped due to DB error: {exc}")
            return tools

        if not disabled_tools:
            return tools

        filtered = self._filter_tools(tools, disabled_tools)
        logger.info(
            f"ToolFilterMiddleware: filtered tools to {len(filtered)} enabled items"
        )
        return filtered

    @staticmethod
    async def _get_disabled_tools_async() -> set[str]:
        """Query disabled tool names from the database.

        Returns:
            set[str]: Set of disabled tool names
        """
        disabled: set[str] = set()
        async with get_async_session() as db_session:
            stmt = select(MCPServerTool.tool_name).where(
                MCPServerTool.is_enabled == False
            )
            result = await db_session.execute(stmt)
            for name in result.scalars().all():
                disabled.add(name)
        logger.debug(f"Disabled tools from DB: {len(disabled)}")
        return disabled

    def _filter_tools(self, tools: Any, disabled_tools: set[str]) -> Any:
        """Filter a sequence or mapping of tools based on disabled names."""

        if isinstance(tools, Mapping):
            filtered: dict[Any, Any] = {}
            for key, tool in tools.items():
                fallback_name = key if isinstance(key, str) else ""
                if not self._is_tool_disabled(tool, disabled_tools, fallback_name):
                    filtered[key] = tool
                else:
                    logger.debug(
                        f"Filtering disabled tool: {self._get_tool_name(tool) or fallback_name}"
                    )
            return filtered

        if isinstance(tools, Sequence) and not isinstance(tools, (str, bytes)):
            enabled: list[Any] = []
            for tool in tools:
                if not self._is_tool_disabled(tool, disabled_tools):
                    enabled.append(tool)
                else:
                    logger.debug(
                        f"Filtering disabled tool: {self._get_tool_name(tool)}"
                    )
            return enabled

        try:
            iterable_tools = list(tools)
        except TypeError:
            return tools

        return [
            tool
            for tool in iterable_tools
            if not self._is_tool_disabled(tool, disabled_tools)
        ]

    def _is_tool_disabled(
        self, tool: Any, disabled_tools: set[str], fallback_name: str | None = None
    ) -> bool:
        name = self._get_tool_name(tool) or (fallback_name or "")
        return bool(name and name in disabled_tools)

    @staticmethod
    def _get_tool_name(tool: Any) -> str:
        if hasattr(tool, "name"):
            return tool.name
        if isinstance(tool, dict) and "name" in tool:
            return tool["name"]
        return ""
