
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
from mcp_anywhere.core.mcp_manager import MCPManager

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
        host = "127.0.0.1"
        port = 8000  # Default uvicorn port
        scheme = "http"  # Internal requests use HTTP
        mcp_url = f"{scheme}://{host}:{port}{Config.MCP_PATH_MOUNT}/"

        logger.info(f"Making internal MCP request to: {mcp_url}")
        logger.info(f"Requested server/tool: {server_id}/{tool_name}")
        logger.info(f"Arguments: {arguments}")

        prefix = MCPManager._format_prefix(server.name, server.id)
        prefixed_tool_name = f"{prefix}_{tool_name}"
        logger.info(f"Derived MCP prefix: {prefix} (from server '{server.name}')")
        logger.info(f"Final tool name: {prefixed_tool_name}")

        jsonrpc_request = {
            "jsonrpc": "2.0",
            "id": str(int(time.time() * 1000)),
            "method": "tools/call",
            "params": {
                "name": prefixed_tool_name,
                "arguments": arguments,
            },
        }

        overall_start = time.time()

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                }

                if "session" in request.cookies:
                    headers["Cookie"] = f"session={request.cookies['session']}"

                logger.info(f"Request headers: {headers}")
                logger.info(f"Request payload: {jsonrpc_request}")

                def _parse_jsonrpc_response(http_response: httpx.Response) -> dict | None:
                    content_type = http_response.headers.get("content-type", "")
                    if "text/event-stream" in content_type:
                        for line in http_response.text.split("\n"):
                            if line.startswith("data: "):
                                json_str = line[6:].strip()
                                if not json_str:
                                    continue
                                try:
                                    return json.loads(json_str)
                                except json.JSONDecodeError:
                                    logger.warning(
                                        f"Failed to parse SSE data line: {json_str}"
                                    )
                                    break
                        return None
                    try:
                        return http_response.json()
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse JSON response body during MCP request")
                        return None

                async def _send_initialized_notification(session_headers: dict) -> None:
                    notify_payload = {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    }
                    notify_response = await client.post(
                        mcp_url,
                        json=notify_payload,
                        headers=session_headers,
                        follow_redirects=True,
                    )
                    logger.info(
                        "Initialized notification status: %s",
                        notify_response.status_code,
                    )
                    if notify_response.status_code >= 400:
                        raise RuntimeError(
                            "Failed to send notifications/initialized "
                            f"(status {notify_response.status_code})"
                        )
                    notify_data = _parse_jsonrpc_response(notify_response) or {}
                    if notify_data.get("error"):
                        raise RuntimeError(
                            "Server returned error for notifications/initialized: "
                            f"{notify_data['error']}"
                        )

                initialize_payload = {
                    "jsonrpc": "2.0",
                    "id": str(int(time.time() * 1000)),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mcp-anywhere-test", "version": "1.0.0"},
                    },
                }

                initialize_response = await client.post(
                    mcp_url,
                    json=initialize_payload,
                    headers=headers,
                    follow_redirects=True,
                )

                logger.info(
                    "Initialize response status: %s",
                    initialize_response.status_code,
                )
                logger.info(
                    "Initialize response headers: %s",
                    dict(initialize_response.headers),
                )
                logger.info(
                    "Initialize response body: %s",
                    initialize_response.text[:500],
                )

                init_data = _parse_jsonrpc_response(initialize_response) or {}
                if initialize_response.status_code != 200:
                    duration_ms = int((time.time() - overall_start) * 1000)
                    return JSONResponse(
                        {
                            "success": False,
                            "error": (
                                "Failed to initialize MCP session "
                                f"(HTTP {initialize_response.status_code})"
                            ),
                            "error_code": (init_data.get("error") or {}).get("code"),
                            "error_data": (init_data.get("error") or {}).get("data"),
                            "duration_ms": duration_ms,
                        }
                    )

                if init_data.get("error"):
                    duration_ms = int((time.time() - overall_start) * 1000)
                    error_info = init_data["error"]
                    return JSONResponse(
                        {
                            "success": False,
                            "error": error_info.get("message", "Initialization error"),
                            "error_code": error_info.get("code"),
                            "error_data": error_info.get("data"),
                            "duration_ms": duration_ms,
                        }
                    )

                mcp_session_id = initialize_response.headers.get("mcp-session-id")
                if not mcp_session_id:
                    result_payload = init_data.get("result") or {}
                    mcp_session_id = (
                        result_payload.get("sessionId")
                        or result_payload.get("session_id")
                    )

                if not mcp_session_id:
                    duration_ms = int((time.time() - overall_start) * 1000)
                    return JSONResponse(
                        {
                            "success": False,
                            "error": "Failed to initialize MCP session: missing session ID",
                            "duration_ms": duration_ms,
                        }
                    )

                session_headers = headers.copy()
                session_headers["mcp-session-id"] = mcp_session_id

                try:
                    await _send_initialized_notification(session_headers)
                except Exception as exc:
                    duration_ms = int((time.time() - overall_start) * 1000)
                    logger.exception("Error sending notifications/initialized")
                    return JSONResponse(
                        {
                            "success": False,
                            "error": str(exc),
                            "duration_ms": duration_ms,
                        }
                    )

                max_attempts = 2
                attempt = 0
                response = None
                duration_ms = 0

                while attempt < max_attempts:
                    attempt += 1
                    call_headers = session_headers.copy()
                    call_start = time.time()
                    response = await client.post(
                        mcp_url,
                        json=jsonrpc_request,
                        headers=call_headers,
                        follow_redirects=True,
                    )
                    duration_ms = int((time.time() - call_start) * 1000)

                    logger.info(f"Response status: {response.status_code}")
                    logger.info(f"Response headers: {dict(response.headers)}")
                    logger.info(f"Response body: {response.text[:500]}")

                    returned_session_id = response.headers.get("mcp-session-id")
                    if (
                        returned_session_id
                        and returned_session_id != session_headers.get("mcp-session-id")
                        and attempt < max_attempts
                    ):
                        logger.info(
                            "Server returned new mcp-session-id: %s, re-initializing",
                            returned_session_id,
                        )
                        session_headers["mcp-session-id"] = returned_session_id
                        try:
                            await _send_initialized_notification(session_headers)
                            continue
                        except Exception as exc:
                            logger.exception("Error re-sending notifications/initialized")
                            return JSONResponse(
                                {
                                    "success": False,
                                    "error": str(exc),
                                    "duration_ms": duration_ms,
                                }
                            )

                    break

                if response is None:
                    duration_ms = int((time.time() - overall_start) * 1000)
                    return JSONResponse(
                        {
                            "success": False,
                            "error": "Failed to contact MCP endpoint",
                            "duration_ms": duration_ms,
                        }
                    )

                if response.status_code == 200:
                    result_data = _parse_jsonrpc_response(response)
                    if not result_data:
                        return JSONResponse(
                            {
                                "success": False,
                                "error": "Failed to parse MCP response",
                                "duration_ms": duration_ms,
                            }
                        )

                    if "error" in result_data:
                        error_info = result_data["error"]
                        expected_schema = None
                        if error_info.get("code") == -32602:
                            try:
                                list_headers = session_headers.copy()
                                tools_list_req = {
                                    "jsonrpc": "2.0",
                                    "id": str(int(time.time() * 1000)),
                                    "method": "tools/list",
                                    "params": {},
                                }

                                list_resp = await client.post(
                                    mcp_url,
                                    json=tools_list_req,
                                    headers=list_headers,
                                    follow_redirects=True,
                                )

                                data_obj = _parse_jsonrpc_response(list_resp) or {}
                                tools = (data_obj.get("result") or {}).get("tools") or []
                                for t in tools:
                                    if t.get("name") == prefixed_tool_name:
                                        expected_schema = (
                                            t.get("input_schema")
                                            or t.get("schema")
                                            or t.get("inputSchema")
                                        )
                                        break
                            except Exception:
                                logger.debug(
                                    "Failed to fetch tools/list for schema",
                                    exc_info=True,
                                )

                        return JSONResponse(
                            {
                                "success": False,
                                "error": error_info.get("message", "Unknown error"),
                                "error_code": error_info.get("code"),
                                "error_data": error_info.get("data"),
                                "expected_schema": expected_schema,
                                "duration_ms": duration_ms,
                            }
                        )

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
                    error_text = response.text[:500]
                    return JSONResponse(
                        {
                            "success": False,
                            "error": f"HTTP {response.status_code}: {error_text}",
                            "duration_ms": duration_ms,
                        }
                    )

            except httpx.TimeoutException:
                duration_ms = int((time.time() - overall_start) * 1000)
                return JSONResponse(
                    {
                        "success": False,
                        "error": "Request timeout (30s)",
                        "duration_ms": duration_ms,
                    }
                )
            except httpx.RequestError as e:
                duration_ms = int((time.time() - overall_start) * 1000)
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

