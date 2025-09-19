"""FastMCP middleware that records tool usage analytics."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.types import CallToolRequestParams
from fastmcp.tools.tool import ToolResult

from mcp_anywhere.logging_config import get_logger
from mcp_anywhere.tool_usage import ToolUsageRecord, record_tool_usage

logger = get_logger(__name__)


@dataclass(slots=True)
class _ServerInfo:
    prefix: str | None
    name: str | None


class ToolUsageLoggingMiddleware(Middleware):
    """Middleware that logs every tools/call request outcome."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next,
    ) -> ToolResult:
        start = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            await self._persist_event(
                context=context,
                duration_ms=duration_ms,
                status="error",
                result=None,
                error=exc,
            )
            raise
        else:
            duration_ms = int((time.perf_counter() - start) * 1000)
            await self._persist_event(
                context=context,
                duration_ms=duration_ms,
                status="success",
                result=result,
                error=None,
            )
            return result

    async def _persist_event(
        self,
        *,
        context: MiddlewareContext[CallToolRequestParams],
        duration_ms: int,
        status: str,
        result: ToolResult | None,
        error: Exception | None,
    ) -> None:
        message = context.message
        full_tool_name = message.name or "unknown"
        server_info = self._resolve_server_info(context, full_tool_name)
        request_type = self._format_request_type(context.method)
        timestamp = context.timestamp or datetime.now(timezone.utc)
        _, tool_basename = self._split_tool_name(full_tool_name)

        client_name = None
        try:
            fastmcp_ctx = context.fastmcp_context
            if fastmcp_ctx is not None:
                client_name = fastmcp_ctx.client_id or fastmcp_ctx.session_id
        except Exception:  # pragma: no cover - defensive logging
            logger.debug("Unable to resolve client identifier", exc_info=True)

        arguments = self._safe_model_dump(message)
        response_payload = None
        if result is not None:
            response_payload = {
                "content": list(result.content),
                "structured_content": result.structured_content,
            }

        error_message = None
        if error is not None:
            error_message = str(error)

        event = ToolUsageRecord(
            timestamp=timestamp,
            request_type=request_type,
            tool_name=tool_basename,
            full_tool_name=full_tool_name,
            status=status,
            processing_ms=duration_ms,
            arguments=arguments,
            response=response_payload,
            error_message=error_message,
            server_id=server_info.prefix,
            server_name=server_info.name,
            client_name=client_name,
        )

        await record_tool_usage(event)

    @staticmethod
    def _safe_model_dump(message: CallToolRequestParams) -> dict[str, Any]:
        try:
            return message.model_dump(exclude_none=True)
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug("Failed to serialize tool call parameters", exc_info=True)
            return {"name": message.name, "arguments": getattr(message, "arguments", {})}

    @staticmethod
    def _format_request_type(method: str | None) -> str:
        if not method:
            return "CallTool"
        segments = [segment.title() for segment in method.split("/") if segment]
        return segments[-1] if segments else "CallTool"

    def _resolve_server_info(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        full_tool_name: str,
    ) -> _ServerInfo:
        prefix, _ = self._split_tool_name(full_tool_name)
        server_name = None

        if prefix and context.fastmcp_context:
            try:
                fastmcp = context.fastmcp_context.fastmcp
                mounted_servers = getattr(fastmcp, "_tool_manager", None)
                if mounted_servers is not None:
                    for mounted in getattr(mounted_servers, "_mounted_servers", []):
                        if getattr(mounted, "prefix", None) == prefix:
                            server_name = getattr(mounted.server, "name", None)
                            break
            except Exception:  # pragma: no cover - do not disrupt tool execution
                logger.debug("Failed to resolve mounted server info", exc_info=True)

        return _ServerInfo(prefix=prefix, name=server_name)

    @staticmethod
    def _split_tool_name(full_tool_name: str) -> tuple[str | None, str]:
        if "_" in full_tool_name:
            prefix, rest = full_tool_name.split("_", 1)
            return prefix, rest
        return None, full_tool_name

    @staticmethod
    def _extract_tool_name(full_tool_name: str) -> str:
        return full_tool_name.split("_", 1)[-1]
