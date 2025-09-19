"""Routes for viewing MCP tool usage analytics."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from mcp_anywhere.logging_config import get_logger
from mcp_anywhere.tool_usage import fetch_recent_tool_usage, fetch_tool_usage_by_id
from mcp_anywhere.web.routes import get_template_context

logger = get_logger(__name__)

templates = Jinja2Templates(directory="src/mcp_anywhere/web/templates")


def _is_authenticated(request: Request) -> bool:
    return bool(request.session.get("user_id"))


async def tool_usage_dashboard(request: Request) -> Response:
    """Render timeline with recent tool usage events."""
    if not _is_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return RedirectResponse(url=login_url, status_code=302)

    try:
        limit_param = request.query_params.get("limit")
        limit = int(limit_param) if limit_param else 200
    except ValueError:
        limit = 200

    logs = await fetch_recent_tool_usage(limit)
    view_model = _prepare_view_model(logs)

    context = get_template_context(request, **view_model)
    return templates.TemplateResponse("logs/tool_usage.html", context)


async def tool_usage_detail(request: Request) -> Response:
    """Render a modal-friendly detail view for a specific log entry."""
    if not _is_authenticated(request):
        return Response(status_code=401)

    log_id = request.path_params.get("log_id")
    if not log_id:
        return Response(status_code=400)

    log_entry = await fetch_tool_usage_by_id(log_id)
    if log_entry is None:
        return Response(status_code=404)

    detail = _build_detail_view(log_entry)
    return templates.TemplateResponse(
        "logs/tool_usage_detail.html",
        {"request": request, "log": detail},
    )


def _prepare_view_model(logs) -> dict[str, Any]:
    grouped: dict[datetime.date, list[dict[str, Any]]] = defaultdict(list)
    summary_map: dict[str, dict[str, Any]] = {}

    for log in logs:
        timestamp = log.timestamp
        date_key = timestamp.date()
        time_label = timestamp.strftime("%H:%M:%S")

        grouped[date_key].append(
            {
                "id": log.id,
                "time": time_label,
                "tool_name": log.tool_name,
                "server_name": log.server_name,
                "full_tool_name": log.full_tool_name,
                "status": log.status,
                "processing_ms": log.processing_ms,
                "request_type": log.request_type,
                "client_name": log.client_name,
                "timestamp_iso": timestamp.isoformat(sep=" "),
            }
        )

        summary = summary_map.setdefault(
            log.full_tool_name,
            {
                "tool_name": log.tool_name,
                "server_name": log.server_name,
                "calls": 0,
                "success": 0,
                "last_used": timestamp,
            },
        )
        summary["calls"] += 1
        if log.status.lower() == "success":
            summary["success"] += 1
        if timestamp > summary["last_used"]:
            summary["last_used"] = timestamp

    grouped_logs = []
    for date_key, items in grouped.items():
        grouped_logs.append(
            {
                "date": date_key,
                "date_label": date_key.strftime("%B %d, %Y"),
                "items": sorted(items, key=lambda item: item["time"], reverse=True),
            }
        )

    grouped_logs.sort(key=lambda entry: entry["date"], reverse=True)

    summary_list = sorted(
        summary_map.values(),
        key=lambda entry: (entry["calls"], entry["last_used"]),
        reverse=True,
    )

    last_updated = logs[0].timestamp if logs else None

    return {
        "grouped_logs": grouped_logs,
        "summary": summary_list,
        "last_updated": last_updated,
    }


def _build_detail_view(log) -> dict[str, Any]:
    timestamp = log.timestamp
    timestamp_label = timestamp.strftime("%B %d, %Y %H:%M:%S")

    return {
        "id": log.id,
        "timestamp": timestamp_label,
        "tool_name": log.tool_name,
        "server_name": log.server_name,
        "full_tool_name": log.full_tool_name,
        "status": log.status,
        "processing_ms": log.processing_ms,
        "request_type": log.request_type,
        "client_name": log.client_name,
        "arguments_json": json.dumps(log.arguments or {}, indent=2),
        "response_json": json.dumps(log.response or {}, indent=2),
        "error_message": log.error_message,
    }


tool_usage_routes = [
    Route("/logs/tools", endpoint=tool_usage_dashboard, methods=["GET"]),
    Route("/logs/tools/{log_id}", endpoint=tool_usage_detail, methods=["GET"]),
]
