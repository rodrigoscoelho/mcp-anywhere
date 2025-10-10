
"""Routes for MCP Server testing and simulation."""

import json
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from mcp_anywhere.config import Config
from mcp_anywhere.database import MCPServer, get_async_session
from mcp_anywhere.logging_config import get_logger
from mcp_anywhere.web.routes import get_template_context

logger = get_logger(__name__)
templates = Jinja2Templates(directory="src/mcp_anywhere/web/templates")


def _is_authenticated(request: Request) -> bool:
    """Check if user is authenticated."""
    return bool(request.session.get("user_id"))


async def test_debug(request: Request) -> JSONResponse:
    """Debug endpoint to check MCP configuration."""
    if not _is_authenticated(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Get MCP manager from app state
    mcp_manager = getattr(request.app.state, "mcp_manager", None)

    debug_info = {
        "mcp_path_mount": Config.MCP_PATH_MOUNT,
        "mcp_path_prefix": Config.MCP_PATH_PREFIX,
        "server_url": Config.SERVER_URL,
        "transport_mode": getattr(request.app.state, "transport_mode", "unknown"),
        "mcp_manager_available": mcp_manager is not None,
        "request_url": str(request.url),
        "request_host": request.url.hostname,
        "request_port": request.url.port,
        "request_scheme": request.url.scheme,
    }

    # Try to list mounted servers
    if mcp_manager:
        debug_info["mounted_servers"] = list(mcp_manager.mounted_servers.keys())

    return JSONResponse(debug_info)


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
    """Execute a tool via the official /mcp endpoint as an external client would."""
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

        # Get server details to validate it exists and is active
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

        # Build the MCP endpoint URL
        # Since we're behind a proxy, we should make the request to localhost
        # but use the internal port where uvicorn is running

        # For internal requests, always use localhost and the actual server port
        # This avoids going through the proxy
        host = "127.0.0.1"
        port = 8000  # Default uvicorn port
        scheme = "http"  # Internal requests use HTTP

        # Build the full MCP URL
        # Use MCP_PATH_MOUNT (without trailing slash) + "/" for the JSON-RPC endpoint
        mcp_url = f"{scheme}://{host}:{port}{Config.MCP_PATH_MOUNT}/"

        logger.info(f"Making internal MCP request to: {mcp_url}")
        logger.info(f"Tool name: {server_id}_{tool_name}")
        logger.info(f"Arguments: {arguments}")

        # The tool name should include the server prefix
        # Format: {server_id}_{tool_name}
        prefixed_tool_name = f"{server_id}_{tool_name}"

        # Create JSON-RPC 2.0 request for tools/call
        # This is the standard MCP protocol format used by all MCP clients
        jsonrpc_request = {
            "jsonrpc": "2.0",
            "id": str(int(time.time() * 1000)),
            "method": "tools/call",
            "params": {
                "name": prefixed_tool_name,
                "arguments": arguments,
            },
        }

        start_time = time.time()

        # Make HTTP POST request to the MCP endpoint
        # This simulates exactly how an external client (like Claude Desktop) would call the tool
        # MCP may require a session ID that is returned in the first response
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Prepare headers
                # MCP requires Accept header with both application/json and text/event-stream
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                }

                # Copy session cookie for authentication
                # This allows the internal request to be authenticated
                if "session" in request.cookies:
                    headers["Cookie"] = f"session={request.cookies['session']}"

                logger.info(f"Request headers: {headers}")
                logger.info(f"Request payload: {jsonrpc_request}")

                # Try at most twice: first attempt may return a session id we must echo back
                max_attempts = 2
                attempt = 0
                mcp_session_id = None

                while attempt < max_attempts:
                    # Add MCP session ID if we have one from a previous attempt
                    if mcp_session_id:
                        headers["mcp-session-id"] = mcp_session_id
                        logger.info(f"Using MCP session ID: {mcp_session_id}")

                    # Make the request to the official /mcp endpoint
                    response = await client.post(
                        mcp_url,
                        json=jsonrpc_request,
                        headers=headers,
                        follow_redirects=True,
                    )

                    duration_ms = int((time.time() - start_time) * 1000)

                    logger.info(f"Response status: {response.status_code}")
                    logger.info(f"Response headers: {dict(response.headers)}")
                    logger.info(f"Response body: {response.text[:500]}")

                    # If server returned a session id header, retry with it
                    returned_session_id = response.headers.get("mcp-session-id")
                    if returned_session_id and not mcp_session_id:
                        logger.info(f"Server returned mcp-session-id: {returned_session_id}, retrying...")
                        mcp_session_id = returned_session_id
                        attempt += 1
                        continue

                    # No session ID needed or we already have it, break the loop
                    break

                # Parse response (outside the retry loop)
                if response.status_code == 200:
                    # Check if response is SSE (text/event-stream) or JSON
                    content_type = response.headers.get("content-type", "")

                    if "text/event-stream" in content_type:
                        # Parse SSE format
                        # Format: "event: message\ndata: {json}\n\n"
                        response_text = response.text
                        logger.info(f"Parsing SSE response: {response_text[:200]}")

                        # Extract JSON from SSE data line
                        result_data = None
                        for line in response_text.split("\n"):
                            if line.startswith("data: "):
                                json_str = line[6:]  # Remove "data: " prefix
                                try:
                                    result_data = json.loads(json_str)
                                    break
                                except json.JSONDecodeError:
                                    logger.warning(f"Failed to parse SSE data line: {json_str}")
                                    continue

                        if not result_data:
                            return JSONResponse(
                                {
                                    "success": False,
                                    "error": "Failed to parse SSE response",
                                    "duration_ms": duration_ms,
                                }
                            )
                    else:
                        # Regular JSON response
                        result_data = response.json()

                    # Check for JSON-RPC error
                    if "error" in result_data:
                        error_info = result_data["error"]
                        return JSONResponse(
                            {
                                "success": False,
                                "error": error_info.get("message", "Unknown error"),
                                "error_code": error_info.get("code"),
                                "error_data": error_info.get("data"),
                                "duration_ms": duration_ms,
                            }
                        )

                    # Extract result from JSON-RPC response
                    tool_result = result_data.get("result", {})

                    return JSONResponse(
                        {
                            "success": True,
                            "result": tool_result,
                            "duration_ms": duration_ms,
                            "timestamp": time.time(),
                        }
                    )
                else:
                    # HTTP error
                    error_text = response.text[:500]  # Limit error text length
                    return JSONResponse(
                        {
                            "success": False,
                            "error": f"HTTP {response.status_code}: {error_text}",
                            "duration_ms": duration_ms,
                        }
                    )

            except httpx.TimeoutException:
                duration_ms = int((time.time() - start_time) * 1000)
                return JSONResponse(
                    {
                        "success": False,
                        "error": "Request timeout (30s)",
                        "duration_ms": duration_ms,
                    }
                )
            except httpx.RequestError as e:
                duration_ms = int((time.time() - start_time) * 1000)
                logger.exception(f"HTTP request error: {e}")
                return JSONResponse(
                    {
                        "success": False,
                        "error": f"Request failed: {str(e)}",
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
    Route("/test/debug", endpoint=test_debug, methods=["GET"]),
    Route("/test/servers/{server_id}/tools", endpoint=get_server_tools, methods=["GET"]),
    Route("/test/execute", endpoint=execute_tool, methods=["POST"]),
]

