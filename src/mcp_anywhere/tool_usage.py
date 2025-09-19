"""Persistence helpers for MCP tool usage analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from mcp_anywhere.database import ToolUsageLog, get_async_session
from mcp_anywhere.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ToolUsageRecord:
    """Data container describing a single tool usage event."""

    timestamp: datetime
    request_type: str
    tool_name: str
    full_tool_name: str
    status: str
    processing_ms: int | None
    arguments: dict[str, Any] | None
    response: dict[str, Any] | None
    error_message: str | None
    server_id: str | None = None
    server_name: str | None = None
    client_name: str | None = None


def _coerce_naive_utc(dt: datetime) -> datetime:
    """Convert any datetime to naive UTC for database storage."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _jsonify(value: Any, *, _depth: int = 0) -> Any:
    """Convert arbitrary objects into JSON-friendly structures."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonify(val, _depth=_depth + 1) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonify(item, _depth=_depth + 1) for item in value]

    if hasattr(value, "model_dump") and _depth < 3:
        try:
            return _jsonify(value.model_dump(), _depth=_depth + 1)
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug("model_dump failed during serialization", exc_info=True)

    if hasattr(value, "__dict__") and _depth < 3:
        try:
            return _jsonify(vars(value), _depth=_depth + 1)
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug("vars() failed during serialization", exc_info=True)

    return str(value)


async def record_tool_usage(event: ToolUsageRecord) -> None:
    """Persist a tool usage event in the database."""
    try:
        async with get_async_session() as session:
            async with session.begin():
                log_row = ToolUsageLog(
                    timestamp=_coerce_naive_utc(event.timestamp),
                    request_type=event.request_type,
                    tool_name=event.tool_name,
                    full_tool_name=event.full_tool_name,
                    status=event.status,
                    processing_ms=event.processing_ms,
                    arguments=_jsonify(event.arguments),
                    response=_jsonify(event.response),
                    error_message=event.error_message,
                    server_id=event.server_id,
                    server_name=event.server_name,
                    client_name=event.client_name,
                )
                session.add(log_row)
    except Exception:  # pragma: no cover - we do not want to break tool calls on logging failure
        logger.exception("Failed to persist tool usage event")


async def fetch_recent_tool_usage(limit: int = 200) -> list[ToolUsageLog]:
    """Return the most recent tool usage entries (defaults to 200)."""
    stmt = (
        select(ToolUsageLog)
        .order_by(ToolUsageLog.timestamp.desc())
        .limit(max(limit, 1))
    )
    async with get_async_session() as session:
        result = await session.execute(stmt)
        return result.scalars().all()


async def fetch_tool_usage_by_id(log_id: str) -> ToolUsageLog | None:
    """Fetch a single tool usage record by ID."""
    stmt = select(ToolUsageLog).where(ToolUsageLog.id == log_id)
    async with get_async_session() as session:
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
