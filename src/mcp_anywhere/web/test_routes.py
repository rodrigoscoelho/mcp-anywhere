"""Routes for MCP Server testing and simulation."""

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from mcp_anywhere.database import MCPServer, MCPServerTool, get_async_session
from mcp_anywhere.logging_config import get_logger
from mcp_anywhere.web.routes import get_template_context

logger = get_logger(__name__)
templates = Jinja2Templates(directory="src/mcp_anywhere/web/templates")


def _is_authenticated(request: Request) -> bool:
    """Check if user is authenticated."""
    return bool(request.session.get("user_id"))


async def test_tools_page(request: Request) -> Response:
    """Render the main testing page."""
    if not _is_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return Response(status_code=302, headers={"Location": login_url})

    try:
        async with get_async_session() as db_session:
            # Get all active servers
            stmt = (
                select(MCPServer)
                .where(MCPServer.is_active == True)
                .order_by(MCPServer.name)
            )
            result = await db_session.execute(stmt)
            servers = result.scalars().all()

        context = get_template_context(
            request,
            servers=servers,
        )
        return templates.TemplateResponse(request, "test/index.html", context)

    except Exception as e:
        logger.exception(f"Error loading test tools page: {e}")
        return templates.TemplateResponse(
            request,
            "500.html",
            {"message": "Error loading test tools page"},
            status_code=500,
        )


async def get_server_tools(request: Request) -> JSONResponse:
    """Get tools for a specific server."""
    if not _is_authenticated(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    server_id = request.path_params.get("server_id")
    if not server_id:
        return JSONResponse({"error": "Server ID required"}, status_code=400)

    try:
        async with get_async_session() as db_session:
            # Get server with tools
            stmt = (
                select(MCPServer)
                .options(selectinload(MCPServer.tools))
                .where(MCPServer.id == server_id)
            )
            result = await db_session.execute(stmt)
            server = result.scalar_one_or_none()

            if not server:
                return JSONResponse(
                    {"error": f"Server '{server_id}' not found"}, status_code=404
                )

            # Filter enabled tools and format response
            tools = [
                {
                    "id": tool.id,
                    "name": tool.tool_name,
                    "description": tool.tool_description or "",
                    "schema": tool.tool_schema or {},
                }
                for tool in server.tools
                if tool.is_enabled
            ]

            return JSONResponse(
                {
                    "server_id": server.id,
                    "server_name": server.name,
                    "tools": tools,
                }
            )

    except Exception as e:
        logger.exception(f"Error fetching tools for server {server_id}: {e}")
        return JSONResponse(
            {"error": "Failed to fetch server tools"}, status_code=500
        )


async def execute_tool(request: Request) -> JSONResponse:
    """Execute a tool on an MCP server by calling it directly through the MCP manager."""
    if not _is_authenticated(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Parse request body
        body = await request.json()
        server_id = body.get("server_id")
        tool_name = body.get("tool_name")
        arguments = body.get("arguments", {})

        if not server_id or not tool_name:
            return JSONResponse(
                {"error": "server_id and tool_name are required"}, status_code=400
            )

        # Get server details
        async with get_async_session() as db_session:
            stmt = select(MCPServer).where(MCPServer.id == server_id)
            result = await db_session.execute(stmt)
            server = result.scalar_one_or_none()

            if not server:
                return JSONResponse(
                    {"error": f"Server '{server_id}' not found"}, status_code=404
                )

            if not server.is_active:
                return JSONResponse(
                    {"error": f"Server '{server.name}' is not active"}, status_code=400
                )

        # Get the MCP manager from app state
        mcp_manager = getattr(request.app.state, "mcp_manager", None)
        if not mcp_manager:
            return JSONResponse(
                {"error": "MCP manager not available"}, status_code=500
            )

        # The tool name should include the server prefix
        # Format: {server_id}_{tool_name}
        prefixed_tool_name = f"{server_id}_{tool_name}"

        start_time = time.time()

        try:
            # Call the tool directly through the MCP manager's router
            # The router has a _tool_manager that can execute tools
            tool_manager = mcp_manager.router._tool_manager

            # Get the tool
            tools = await tool_manager.get_tools()
            if prefixed_tool_name not in tools:
                return JSONResponse(
                    {
                        "success": False,
                        "error": f"Tool '{tool_name}' not found on server '{server.name}'",
                        "duration_ms": 0,
                    }
                )

            # Execute the tool
            tool_func = tools[prefixed_tool_name]
            result = await tool_func(**arguments)

            duration_ms = int((time.time() - start_time) * 1000)

            # Format the result
            # MCP tools return a list of content items
            if hasattr(result, 'content'):
                # It's a proper MCP result
                result_data = {
                    "content": [
                        {
                            "type": item.type,
                            "text": item.text if hasattr(item, 'text') else str(item)
                        }
                        for item in result.content
                    ]
                }
            else:
                # Fallback for other result types
                result_data = {"content": [{"type": "text", "text": str(result)}]}

            return JSONResponse(
                {
                    "success": True,
                    "result": result_data,
                    "duration_ms": duration_ms,
                    "timestamp": time.time(),
                }
            )

        except TypeError as e:
            # Handle argument errors
            duration_ms = int((time.time() - start_time) * 1000)
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Invalid arguments: {str(e)}",
                    "duration_ms": duration_ms,
                }
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.exception(f"Error executing tool {prefixed_tool_name}: {e}")
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Tool execution failed: {str(e)}",
                    "duration_ms": duration_ms,
                }
            )

    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON in request body"}, status_code=400)
    except Exception as e:
        logger.exception(f"Error executing tool: {e}")
        return JSONResponse(
            {"success": False, "error": f"Internal error: {str(e)}"}, status_code=500
        )


# Define routes
test_routes = [
    Route("/test", endpoint=test_tools_page, methods=["GET"]),
    Route("/test/servers/{server_id}/tools", endpoint=get_server_tools, methods=["GET"]),
    Route("/test/execute", endpoint=execute_tool, methods=["POST"]),
]

