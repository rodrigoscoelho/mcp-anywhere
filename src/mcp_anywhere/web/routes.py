from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from starlette.datastructures import FormData, UploadFile
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from mcp_anywhere.claude_analyzer import AsyncClaudeAnalyzer
from mcp_anywhere.container.manager import ContainerManager
from mcp_anywhere.database import MCPServer, MCPServerTool, get_async_session
from mcp_anywhere.database_utils import store_server_tools
from mcp_anywhere.logging_config import get_logger
from mcp_anywhere.server_guidance import (
    CONTAINER_EXECUTION_NOTE,
    FIELD_GUIDANCE,
)
from mcp_anywhere.web.forms import AnalyzeFormData, ServerFormData

logger = get_logger(__name__)
templates = Jinja2Templates(directory="src/mcp_anywhere/web/templates")


class CurrentUser:
    """Simple current user object for template context."""

    def __init__(self, user_id: str | None = None, username: str | None = None) -> None:
        self.user_id: str | None = user_id
        self.username: str | None = username
        self.is_authenticated = bool(user_id)


def get_current_user(request: Request) -> CurrentUser:
    """Get current user from session."""
    user_id = request.session.get("user_id")
    username = request.session.get("username")
    return CurrentUser(user_id, username)


def get_template_context(request: Request, **kwargs) -> dict[str, Any]:
    """Get base template context with current user and transport mode."""
    # Get transport mode from app state
    transport_mode = getattr(request.app.state, "transport_mode", "http")

    context: dict[str, Any] = {
        "request": request,
        "current_user": get_current_user(request),
        "transport_mode": transport_mode,
        **kwargs,
    }
    return context


# Utility helpers ---------------------------------------------------------

def _as_optional_str(value: Any) -> str | None:
    """Coerce form-like values to optional strings."""
    if isinstance(value, str):
        return value
    if isinstance(value, UploadFile):
        return value.filename
    return None


def _coerce_str(value: Any, default: str = "") -> str:
    """Return a string for form values, falling back to a default."""
    text = _as_optional_str(value)
    return text if text is not None else default


def _get_form_value(source: ServerFormData | FormData | None, key: str) -> str | None:
    """Extract a field value from form-like objects while preserving blanks."""
    if source is None:
        return None
    if isinstance(source, ServerFormData):
        value = getattr(source, key, None)
        return value if isinstance(value, str) else None
    if isinstance(source, FormData):
        return _as_optional_str(source.get(key))
    if isinstance(source, Mapping):
        return _as_optional_str(source.get(key))
    attr = getattr(source, key, None)
    return attr if isinstance(attr, str) else None


def _extract_env_variables_from_form(form_data: ServerFormData | FormData | None) -> list[dict[str, Any]]:
    """Normalize environment variables from posted form data."""
    if form_data is None:
        return []

    if isinstance(form_data, ServerFormData):
        env_vars: list[dict[str, Any]] = []
        for env in form_data.env_variables:
            env_vars.append(
                {
                    "key": env.get("key", ""),
                    "value": env.get("value", ""),
                    "description": env.get("description", ""),
                    "required": bool(env.get("required", True)),
                }
            )
        return env_vars

    if isinstance(form_data, FormData):
        env_vars: list[dict[str, Any]] = []

        indices: set[int] = set()
        for field_name in form_data.keys():
            if field_name.startswith("env_key_"):
                suffix = field_name.removeprefix("env_key_")
                if suffix.isdigit():
                    indices.add(int(suffix))

        for index in sorted(indices):
            raw_key = _as_optional_str(form_data.get(f"env_key_{index}"))
            key = raw_key.strip() if raw_key else ""
            raw_value = _as_optional_str(form_data.get(f"env_value_{index}"))
            value = raw_value if raw_value is not None else ""
            raw_description = _as_optional_str(form_data.get(f"env_desc_{index}"))
            description = raw_description if raw_description is not None else ""
            required_raw = (_as_optional_str(form_data.get(f"env_required_{index}")) or "").lower()
            required = required_raw == "true"
            if key:
                env_vars.append(
                    {
                        "key": key,
                        "value": value,
                        "description": description,
                        "required": required,
                    }
                )

        if env_vars:
            return env_vars

        try:
            legacy_keys = form_data.getlist("env_keys[]")
        except AttributeError:
            legacy_keys = []
        for raw_key in legacy_keys:
            if not isinstance(raw_key, str):
                continue
            key = raw_key.strip()
            if not key:
                continue
            value = _coerce_str(form_data.get(f"env_value_{raw_key}"))
            description = _coerce_str(form_data.get(f"env_desc_{raw_key}"))
            required_raw = (_as_optional_str(form_data.get(f"env_required_{raw_key}")) or "true").lower()
            required = required_raw == "true"
            env_vars.append(
                {
                    "key": key,
                    "value": value,
                    "description": description,
                    "required": required,
                }
            )

        return env_vars

    # Unknown form type; return empty list
    return []


def _with_query_params(url: str, **params: str) -> str:
    """Return *url* with updated query parameters."""

    parsed = urlparse(url)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in params.items():
        if value is None:
            continue
        existing[key] = value
    new_query = urlencode(existing)
    return urlunparse(parsed._replace(query=new_query))


def build_add_server_context(
    request: Request,
    github_url: str | None = None,
    analysis: dict | None = None,
    form_data: ServerFormData | FormData | None = None,
    errors: dict | None = None,
    error: str | None = None,
    warning: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Build the template context for the add server page/partials."""
    analyze_trigger = None
    if form_data is not None:
        if isinstance(form_data, FormData):
            analyze_trigger = form_data.get("analyze")
        elif isinstance(form_data, Mapping):
            analyze_trigger = form_data.get("analyze")
        else:
            analyze_trigger = getattr(form_data, "analyze", None)
    is_analyze_request = analyze_trigger is not None

    form_values = {
        "github_url": github_url or "",
        "name": "",
        "description": "",
        "runtime_type": "docker",
        "install_command": "",
        "start_command": "",
    }

    if analysis:
        for key in ("name", "description", "runtime_type", "install_command", "start_command"):
            if key in analysis and analysis[key] is not None:
                form_values[key] = analysis[key] or ""

    for key in form_values.keys():
        value = _get_form_value(form_data, key)
        if value is None:
            continue
        if is_analyze_request and key != "github_url" and value == "":
            continue
        form_values[key] = value

    env_entries = _extract_env_variables_from_form(form_data)
    if not env_entries and analysis and analysis.get("env_variables"):
        env_entries = [
            {
                "key": item.get("key", ""),
                "value": "",
                "description": item.get("description", ""),
                "required": item.get("required", True),
            }
            for item in analysis.get("env_variables", [])
            if item.get("key")
        ]

    mode_value = mode or _get_form_value(form_data, "config_mode") or "auto"
    if isinstance(mode_value, str):
        selected_mode = mode_value.lower()
    else:
        selected_mode = "auto"
    if selected_mode not in {"auto", "manual"}:
        selected_mode = "auto"

    context = get_template_context(
        request,
        github_url=form_values["github_url"],
        analysis=analysis,
        form_values=form_values,
        env_entries=env_entries,
        field_guidance=FIELD_GUIDANCE,
        container_note=CONTAINER_EXECUTION_NOTE,
        config_mode=selected_mode,
        errors=errors,
        error=error,
        warning=warning,
    )
    return context


def get_mcp_manager(request: Request):
    """Get the MCP manager from the application state."""
    return getattr(request.app.state, "mcp_manager", None)


async def homepage(request: Request) -> HTMLResponse:
    """Renders the homepage, displaying a list of configured MCP servers."""
    try:
        async with get_async_session() as db_session:
            stmt = (
                select(MCPServer)
                .options(selectinload(MCPServer.tools))
                .order_by(MCPServer.is_active.desc(), MCPServer.name)
            )
            result = await db_session.execute(stmt)
            servers = result.scalars().all()

        error_message = request.query_params.get("error")

        return templates.TemplateResponse(
            request,
            "index.html",
            get_template_context(
                request,
                servers=servers,
                error=error_message,
            ),
        )

    except (RuntimeError, ValueError, ConnectionError) as e:
        # Log error and show empty server list
        logger.exception(f"Error loading servers: {e}")
        return templates.TemplateResponse(
            request,
            "index.html",
            get_template_context(request, servers=[], error=str(e)),
        )


async def server_detail(request: Request) -> HTMLResponse:
    """Show server details including tools."""
    server_id = request.path_params["server_id"]

    try:
        async with get_async_session() as db_session:
            # Get server with secret files
            stmt = (
                select(MCPServer)
                .options(selectinload(MCPServer.secret_files))
                .where(MCPServer.id == server_id)
            )
            result = await db_session.execute(stmt)
            server = result.scalar_one_or_none()

            if not server:
                return templates.TemplateResponse(
                    request,
                    "404.html",
                    {"message": f"Server '{server_id}' not found"},
                    status_code=404,
                )

            # Get tools for this server
            tools_stmt = (
                select(MCPServerTool)
                .where(MCPServerTool.server_id == server_id)
                .order_by(MCPServerTool.tool_name)
            )
            tools_result = await db_session.execute(tools_stmt)
            tools = tools_result.scalars().all()

        return templates.TemplateResponse(
            request,
            "servers/detail.html",
            get_template_context(request, server=server, tools=tools),
        )

    except (RuntimeError, ValueError, ConnectionError) as e:
        logger.exception(f"Error loading server details for {server_id}: {e}")
        return templates.TemplateResponse(
            request,
            "500.html",
            {"message": "Error loading server details"},
            status_code=500,
        )


async def delete_server(request: Request) -> RedirectResponse | HTMLResponse:
    """Delete a server and remove it from MCP manager."""
    server_id = request.path_params["server_id"]

    try:
        async with get_async_session() as db_session:
            # Get server first
            stmt = select(MCPServer).where(MCPServer.id == server_id)
            result = await db_session.execute(stmt)
            server = result.scalar_one_or_none()

            if not server:
                return templates.TemplateResponse(
                    request,
                    "404.html",
                    {"message": f"Server '{server_id}' not found"},
                    status_code=404,
                )

            server_name = server.name

            # Clean up secret files before deleting server
            from mcp_anywhere.security.file_manager import SecureFileManager

            file_manager = SecureFileManager()
            file_manager.cleanup_server_files(server_id)

            # Delete the server (cascade will handle tools and secret files)
            await db_session.delete(server)
            await db_session.commit()

            # Remove from MCP manager
            mcp_manager = get_mcp_manager(request)
            if mcp_manager:
                mcp_manager.remove_server(server_id)

            logger.info(f'Server "{server_name}" deleted successfully!')

        # Redirect to home page
        return RedirectResponse(url="/", status_code=302)

    except (RuntimeError, ValueError, ConnectionError, IntegrityError) as e:
        logger.exception(f"Error deleting server {server_id}: {e}")
        return templates.TemplateResponse(
            request, "500.html", {"message": "Error deleting server"}, status_code=500
        )


async def add_server_get(request: Request) -> HTMLResponse:
    """Display the add server form."""
    mode_param = request.query_params.get("mode", "").lower()
    initial_mode = mode_param if mode_param in {"auto", "manual"} else None
    context = build_add_server_context(request, mode=initial_mode)
    return templates.TemplateResponse(request, "servers/add.html", context)


async def edit_server_get(request: Request) -> HTMLResponse:
    """Display the edit server form."""
    server_id = request.path_params["server_id"]

    try:
        async with get_async_session() as db_session:
            # Get server details
            stmt = select(MCPServer).where(MCPServer.id == server_id)
            result = await db_session.execute(stmt)
            server = result.scalar_one_or_none()

            if not server:
                return templates.TemplateResponse(
                    request,
                    "404.html",
                    get_template_context(
                        request, message=f"Server '{server_id}' not found"
                    ),
                    status_code=404,
                )

        return templates.TemplateResponse(
            request, "servers/edit.html", get_template_context(request, server=server)
        )

    except (RuntimeError, ValueError, ConnectionError) as e:
        logger.exception(f"Error loading server {server_id} for edit: {e}")
        return templates.TemplateResponse(
            request,
            "500.html",
            get_template_context(request, message="Error loading server"),
            status_code=500,
        )


async def edit_server_post(request: Request) -> Response:
    """Handle edit server form submission."""
    server_id = request.path_params["server_id"]
    form_data = await request.form()

    try:
        # Handle environment variables from form (new indexed format)
        server_data = await create_server_post_form_data(form_data)

        # Update server in database
        async with get_async_session() as db_session:
            stmt = select(MCPServer).where(MCPServer.id == server_id)
            result = await db_session.execute(stmt)
            server = result.scalar_one_or_none()

            if not server:
                return templates.TemplateResponse(
                    request,
                    "404.html",
                    {"message": f"Server '{server_id}' not found"},
                    status_code=404,
                )

            # Update server fields
            server.name = server_data.name
            server.github_url = server_data.github_url
            server.description = server_data.description
            server.runtime_type = server_data.runtime_type
            server.install_command = server_data.install_command or ""
            server.start_command = server_data.start_command
            server.env_variables = server_data.env_variables

            await db_session.commit()
            logger.info(f'Server "{server.name}" updated successfully!')

            # Rebuild the server after configuration changes
            try:
                # Update build status
                server.build_status = "building"
                await db_session.commit()

                # Build the Docker image
                container_manager = ContainerManager()
                image_tag = container_manager.build_server_image(server)

                # Update server with success
                server.build_status = "built"
                server.image_tag = image_tag
                server.build_error = None
                await db_session.commit()

                # Re-add server to MCP manager and get updated tools
                mcp_manager = get_mcp_manager(request)
                if mcp_manager:
                    # Remove old server first (ignore errors - server might not exist)
                    try:
                        mcp_manager.remove_server(server.id)
                    except Exception as e:
                        logger.warning(f"Failed to remove old server {server.id}: {e}")

                    # Clean up any existing container before re-adding
                    container_name = container_manager._get_container_name(
                        server.id, server.name
                    )
                    container_manager._cleanup_existing_container(container_name)
                    # Add updated server and discover tools
                    discovered_tools = await mcp_manager.add_server(server)
                    await store_server_tools(db_session, server, discovered_tools)
                    await db_session.commit()

                logger.info(f'Server "{server.name}" rebuilt successfully after edit!')

            except (RuntimeError, ValueError, ConnectionError, OSError) as e:
                # Check if this is a server startup error (credentials, config issues)
                error_msg = str(e)
                if "Server startup failed:" in error_msg:
                    # Log credential/config errors without full backtrace
                    logger.error(
                        f"Server configuration error for {server.name}: {error_msg}"
                    )
                else:
                    # Log unexpected errors with full backtrace for debugging
                    logger.exception(f"Failed to rebuild image for {server.name}: {e}")

                server.build_status = "failed"
                server.build_error = error_msg
                await db_session.commit()

        # Redirect to server detail page (HTMX compatible)
        if request.headers.get("HX-Request"):
            response = HTMLResponse("", status_code=200)
            response.headers["HX-Redirect"] = f"/servers/{server_id}"
            return response
        else:
            return RedirectResponse(url=f"/servers/{server_id}", status_code=302)

    except ValidationError as e:
        errors = {}
        for error in e.errors():
            field = error["loc"][0]
            errors[field] = [error["msg"]]

        # Re-fetch server for form
        async with get_async_session() as db_session:
            stmt = select(MCPServer).where(MCPServer.id == server_id)
            result = await db_session.execute(stmt)
            server = result.scalar_one_or_none()

        return templates.TemplateResponse(
            request,
            "servers/edit.html",
            get_template_context(request, server=server, errors=errors),
        )

    except (
        RuntimeError,
        ValueError,
        ConnectionError,
        IntegrityError,
    ) as e:
        logger.exception(f"Error updating server {server_id}: {e}")
        return templates.TemplateResponse(
            request,
            "500.html",
            get_template_context(request, message="Error updating server"),
            status_code=500,
        )


async def create_server_post_form_data(form_data: FormData) -> ServerFormData:
    """Create ServerFormData from form data, handling manual and analyzed inputs."""
    env_variables = _extract_env_variables_from_form(form_data)
    server_data = ServerFormData(
        name=_coerce_str(form_data.get("name")),
        github_url=_coerce_str(form_data.get("github_url")),
        description=_as_optional_str(form_data.get("description")),
        runtime_type=_coerce_str(form_data.get("runtime_type")),
        install_command=_as_optional_str(form_data.get("install_command")),
        start_command=_coerce_str(form_data.get("start_command")),
        env_variables=env_variables,
    )
    return server_data


async def toggle_server(request: Request) -> Response:
    """Toggle a server's active state and update mounts accordingly."""

    if request.method != "POST":
        return RedirectResponse(url="/", status_code=302)

    server_id = request.path_params.get("server_id")
    if not server_id:
        return RedirectResponse(url="/", status_code=302)

    form = await request.form()
    redirect_to = _coerce_str(form.get("redirect_to")) or request.headers.get(
        "referer", f"/servers/{server_id}"
    )
    layout = _coerce_str(form.get("layout")) or "default"

    server: MCPServer | None = None
    error_message: str | None = None

    try:
        async with get_async_session() as db_session:
            stmt = (
                select(MCPServer)
                .options(
                    selectinload(MCPServer.secret_files), selectinload(MCPServer.tools)
                )
                .where(MCPServer.id == server_id)
            )
            result = await db_session.execute(stmt)
            server = result.scalar_one_or_none()

            if not server:
                return templates.TemplateResponse(
                    request,
                    "404.html",
                    get_template_context(
                        request, message=f"Server '{server_id}' not found"
                    ),
                    status_code=404,
                )

            server.is_active = not server.is_active
            await db_session.commit()

            mcp_manager = get_mcp_manager(request)
            container_manager = getattr(request.app.state, "container_manager", None)

            if mcp_manager:
                if server.is_active and container_manager is not None:
                    await db_session.refresh(server, ["secret_files"])
                    try:
                        discovered_tools = await mcp_manager.add_server(server)
                        if discovered_tools:
                            await store_server_tools(db_session, server, discovered_tools)
                        await db_session.commit()
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.exception(
                            f"Failed to activate server '{server.name}': {exc}"
                        )
                        server.is_active = False
                        await db_session.commit()
                        error_message = "Failed to activate server. Check server logs."
                elif not server.is_active:
                    try:
                        mcp_manager.remove_server(server.id)
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.debug(
                            f"Failed to remove server '{server.id}' from manager: {exc}"
                        )

    except (RuntimeError, ValueError, ConnectionError, IntegrityError) as e:
        logger.exception(f"Error toggling server {server_id}: {e}")
        return templates.TemplateResponse(
            request,
            "500.html",
            get_template_context(request, message="Error toggling server"),
            status_code=500,
        )

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request,
            "partials/server_toggle.html",
            get_template_context(
                request,
                server=server,
                layout=layout,
                redirect_to=redirect_to,
                toggle_error=error_message,
            ),
        )

    if error_message:
        redirect_to = _with_query_params(redirect_to, error=error_message)

    return RedirectResponse(url=redirect_to, status_code=302)


async def toggle_tool(request: Request) -> HTMLResponse:
    """Toggle tool enabled/disabled status via HTMX."""
    server_id = request.path_params["server_id"]
    tool_id = request.path_params["tool_id"]

    try:
        async with get_async_session() as db_session:
            # Get the tool
            stmt = select(MCPServerTool).where(
                MCPServerTool.id == tool_id, MCPServerTool.server_id == server_id
            )
            result = await db_session.execute(stmt)
            tool = result.scalar_one_or_none()

            if not tool:
                return templates.TemplateResponse(
                    request,
                    "404.html",
                    get_template_context(
                        request, message=f"Tool '{tool_id}' not found"
                    ),
                    status_code=404,
                )

            # Toggle the enabled status
            tool.is_enabled = not tool.is_enabled
            await db_session.commit()

            logger.info(
                f'Tool "{tool.tool_name}" {"enabled" if tool.is_enabled else "disabled"}'
            )

        # Return just the updated toggle switch HTML for HTMX
        return templates.TemplateResponse(
            request,
            "partials/tool_toggle.html",
            get_template_context(request, tool=tool, server_id=server_id),
        )

    except (RuntimeError, ValueError, ConnectionError, IntegrityError) as e:
        logger.exception(f"Error toggling tool {tool_id}: {e}")
        return templates.TemplateResponse(
            request,
            "500.html",
            get_template_context(request, message="Error toggling tool"),
            status_code=500,
        )


async def handle_claude_connection_error(
    request: Request, github_url: str, error: ConnectionError
) -> HTMLResponse:
    """Handle Claude analysis connection failures with fallback."""
    logger.warning(f"Claude analysis failed for {github_url}: {error}")

    analysis = {
        "name": "analyzed-server",
        "description": "Claude analysis unavailable - please fill manually",
        "runtime_type": "docker",
        "install_command": "",
        "start_command": "echo 'placeholder'",
    }

    warning_msg = "Repository analysis failed. Please fill out the form manually."

    context = build_add_server_context(
        request, github_url=github_url, analysis=analysis, warning=warning_msg
    )
    template_name = (
        "partials/analysis_result.html"
        if request.headers.get("HX-Request")
        else "servers/add.html"
    )
    return templates.TemplateResponse(request, template_name, context)


async def handle_claude_config_error(
    request: Request, github_url: str, error: ValueError
) -> HTMLResponse:
    """Handle Claude analyzer configuration errors."""
    logger.error(f"Claude analyzer configuration error: {error}")
    error_msg = f"Repository analysis is not configured: {str(error)}. Please check your ANTHROPIC_API_KEY."

    context = build_add_server_context(
        request, github_url=github_url, error=error_msg
    )
    template_name = (
        "partials/analysis_result.html"
        if request.headers.get("HX-Request")
        else "servers/add.html"
    )
    return templates.TemplateResponse(request, template_name, context)


async def handle_claude_unexpected_error(
    request: Request, github_url: str, error: Exception
) -> HTMLResponse:
    """Handle unexpected errors during Claude analysis."""
    logger.error(f"Unexpected error during analysis: {error}")
    error_msg = f"Analysis failed: {str(error)}"

    context = build_add_server_context(
        request, github_url=github_url, error=error_msg
    )
    template_name = (
        "partials/analysis_result.html"
        if request.headers.get("HX-Request")
        else "servers/add.html"
    )
    return templates.TemplateResponse(request, template_name, context)


async def handle_analyze_validation_error(
    request: Request, form_data, error: ValidationError
) -> HTMLResponse:
    """Handle form validation errors during analysis."""
    logger.error(f"Form validation error: {error}")
    errors = {}
    for err in error.errors():
        field = err["loc"][0]
        errors[field] = [err["msg"]]

    context = build_add_server_context(
        request,
        github_url=_coerce_str(form_data.get("github_url")),
        form_data=form_data,
        errors=errors,
    )
    template_name = (
        "partials/analysis_result.html"
        if request.headers.get("HX-Request")
        else "servers/add.html"
    )
    return templates.TemplateResponse(request, template_name, context)


async def handle_analyze_general_error(
    request: Request, form_data, error: Exception
) -> HTMLResponse:
    """Handle general errors during analysis."""
    logger.error(f"Unexpected error in analyze handler: {error}")
    error_msg = f"Unexpected error: {str(error)}"

    context = build_add_server_context(
        request,
        github_url=_coerce_str(form_data.get("github_url")),
        form_data=form_data,
        error=error_msg,
    )
    template_name = (
        "partials/analysis_result.html"
        if request.headers.get("HX-Request")
        else "servers/add.html"
    )
    return templates.TemplateResponse(request, template_name, context)


async def handle_analyze_repository(request: Request, form_data) -> HTMLResponse:
    """Handle repository analysis request."""
    logger.info(f"Analyze button clicked for URL: {_coerce_str(form_data.get('github_url'))}")
    try:
        analyze_data = AnalyzeFormData(github_url=_coerce_str(form_data.get("github_url")))
        logger.info("Form data validated successfully")

        # Use the real Claude analyzer
        try:
            logger.info("Initializing AsyncClaudeAnalyzer...")
            analyzer = AsyncClaudeAnalyzer()
            logger.info(f"Starting repository analysis for: {analyze_data.github_url}")
            analysis = await analyzer.analyze_repository(analyze_data.github_url)
            logger.info("Analysis completed successfully")

            # Check if this is an HTMX request
            context = build_add_server_context(
                request,
                github_url=analyze_data.github_url,
                analysis=analysis,
                form_data=form_data,
            )
            template_name = (
                "partials/analysis_result.html"
                if request.headers.get("HX-Request")
                else "servers/add.html"
            )
            return templates.TemplateResponse(request, template_name, context)

        except ConnectionError as e:
            return await handle_claude_connection_error(
                request=request, github_url=analyze_data.github_url, error=e
            )

        except ValueError as e:
            return await handle_claude_config_error(
                request=request, github_url=analyze_data.github_url, error=e
            )

        except (RuntimeError, ValueError, ConnectionError, OSError) as e:
            return await handle_claude_unexpected_error(
                request=request, github_url=analyze_data.github_url, error=e
            )

    except ValidationError as e:
        return await handle_analyze_validation_error(
            request=request, form_data=form_data, error=e
        )

    except (RuntimeError, ValueError, ConnectionError) as e:
        return await handle_analyze_general_error(
            request=request, form_data=form_data, error=e
        )


async def handle_save_server(request: Request, form_data) -> Response:
    """Handle server save request."""
    try:
        # Handle environment variables from form (new indexed format)
        server_data = await create_server_post_form_data(form_data)

        # Create server in database
        async with get_async_session() as db_session:
            server = MCPServer(
                name=server_data.name,
                github_url=server_data.github_url,
                description=server_data.description,
                runtime_type=server_data.runtime_type,
                install_command=server_data.install_command or "",
                start_command=server_data.start_command,
                env_variables=server_data.env_variables,
            )

            db_session.add(server)
            await db_session.commit()
            await db_session.refresh(server)  # Get the generated ID

            try:
                # Update build status
                server.build_status = "building"
                await db_session.commit()

                # Build the Docker image
                container_manager = ContainerManager()
                image_tag = container_manager.build_server_image(server)

                # Update server with success
                server.build_status = "built"
                server.image_tag = image_tag
                server.build_error = None
                await db_session.commit()

                # Refresh server with eager loading of relationships
                await db_session.refresh(server, ["secret_files"])

                # Add server to MCP manager and get tools
                mcp_manager = get_mcp_manager(request)
                if mcp_manager:
                    # Clean up any existing container before adding
                    container_name = container_manager._get_container_name(
                        server.id, server.name
                    )
                    container_manager._cleanup_existing_container(container_name)
                    discovered_tools = await mcp_manager.add_server(server)
                    await store_server_tools(db_session, server, discovered_tools)

                logger.info(f'Server "{server.name}" added and built successfully!')

            except (RuntimeError, ValueError, ConnectionError, OSError) as e:
                # Check if this is a server startup error (credentials, config issues)
                error_msg = str(e)
                if "Server startup failed:" in error_msg:
                    # Log credential/config errors without full backtrace
                    logger.error(
                        f"Server configuration error for {server.name}: {error_msg}"
                    )
                else:
                    # Log unexpected errors with full backtrace for debugging
                    logger.error(f"Failed to build image for {server.name}: {e}")

                server.build_status = "failed"
                server.build_error = error_msg
                await db_session.commit()

            # Redirect to home page (HTMX compatible)
            if request.headers.get("HX-Request"):
                response = HTMLResponse("", status_code=200)
                response.headers["HX-Redirect"] = "/"
                return response
            else:
                return RedirectResponse(url="/", status_code=302)

    except ValidationError as e:
        errors = {}
        for error in e.errors():
            field = error["loc"][0]
            errors[field] = [error["msg"]]

        context = build_add_server_context(
            request, form_data=form_data, errors=errors
        )
        return templates.TemplateResponse(request, "servers/add.html", context)

    except IntegrityError:
        context = build_add_server_context(
            request,
            form_data=form_data,
            error="A server with this name already exists.",
        )
        return templates.TemplateResponse(request, "servers/add.html", context)

    except (
        RuntimeError,
        ValueError,
        ConnectionError,
        IntegrityError,
    ) as e:
        logger.exception(f"Error saving server: {e}")
        context = build_add_server_context(
            request,
            form_data=form_data,
            error="Error saving server. Please try again.",
        )
        return templates.TemplateResponse(request, "servers/add.html", context)


async def add_server_post(request: Request) -> Response:
    """Handle add server form submission."""
    form_data = await request.form()

    # Handle analyze button
    if "analyze" in form_data:
        return await handle_analyze_repository(request, form_data)

    # Handle save button
    elif "save" in form_data:
        return await handle_save_server(request, form_data)

    # Invalid form submission
    context = build_add_server_context(request, form_data=form_data)
    return templates.TemplateResponse(request, "servers/add.html", context)


async def add_server(request: Request) -> Response:
    """Handle both GET and POST for /servers/add."""
    if request.method == "GET":
        return await add_server_get(request)
    else:
        return await add_server_post(request)


async def edit_server(request: Request):
    """Handle both GET and POST for /servers/{server_id}/edit."""
    if request.method == "GET":
        return await edit_server_get(request)
    else:
        return await edit_server_post(request)


async def rebuild_server(request: Request) -> Response:
    """Trigger a full rebuild of the MCP server container/image."""
    if request.method != "POST":
        return RedirectResponse(url="/", status_code=302)

    server_id = request.path_params.get("server_id")
    if not server_id:
        return RedirectResponse(url="/", status_code=302)

    async with get_async_session() as db_session:
        stmt = (
            select(MCPServer)
            .options(selectinload(MCPServer.secret_files), selectinload(MCPServer.tools))
            .where(MCPServer.id == server_id)
        )
        result = await db_session.execute(stmt)
        server = result.scalar_one_or_none()

        if not server:
            logger.warning(f"Rebuild requested for unknown server id={server_id}")
            return RedirectResponse(url="/", status_code=302)

        container_manager = ContainerManager()
        mcp_manager = get_mcp_manager(request)

        try:
            server.build_status = "building"
            server.build_logs = "Rebuilding..."
            server.build_error = None
            await db_session.commit()

            # Build fresh image (reinstalls dependencies)
            image_tag = container_manager.build_server_image(server)
            server.image_tag = image_tag
            server.build_status = "built"
            server.build_logs = f"Rebuilt image {image_tag}"
            await db_session.commit()

            if mcp_manager:
                # Ensure latest relationships for mount
                await db_session.refresh(server, ["secret_files"])

                try:
                    mcp_manager.remove_server(server.id)
                except Exception as exc:
                    logger.debug(
                        f"No existing mount to remove for server {server.id}: {exc}"
                    )

                container_name = container_manager._get_container_name(
                    server.id, server.name
                )
                container_manager._cleanup_existing_container(container_name)

                discovered_tools = await mcp_manager.add_server(server)
                await store_server_tools(db_session, server, discovered_tools)
                await db_session.commit()

            logger.info(f"Server '{server.name}' rebuilt successfully by request")

        except (RuntimeError, ValueError, ConnectionError, OSError) as exc:
            error_message = str(exc)
            server.build_status = "failed"
            server.build_error = error_message
            server.build_logs = error_message
            await db_session.commit()
            logger.error(f"Failed to rebuild server '{server.name}': {error_message}")

    if request.headers.get("HX-Request"):
        response = HTMLResponse("", status_code=200)
        response.headers["HX-Redirect"] = f"/servers/{server_id}"
        return response

    return RedirectResponse(url=f"/servers/{server_id}", status_code=302)


async def favicon(_request: Request):
    """Handle favicon.ico requests with a 204 No Content response to silence 404s."""
    return Response(status_code=204)


async def health(_request: Request) -> Response:
    """Lightweight health check endpoint.

    Returns:
        Response: Plain text "ok" with HTTP 200 status.
    """
    return Response(content="ok", media_type="text/plain", status_code=200)


routes = [
    Route("/", endpoint=homepage),
    Route("/health", endpoint=health, methods=["GET"]),
    Route("/favicon.ico", endpoint=favicon, methods=["GET"]),
    Route("/servers/add", endpoint=add_server, methods=["GET", "POST"]),
    Route("/servers/{server_id}", endpoint=server_detail, methods=["GET"]),
    Route("/servers/{server_id}/edit", endpoint=edit_server, methods=["GET", "POST"]),
    Route("/servers/{server_id}/toggle", endpoint=toggle_server, methods=["POST"]),
    Route("/servers/{server_id}/rebuild", endpoint=rebuild_server, methods=["POST"]),
    Route(
        "/servers/{server_id}/tools/{tool_id}/toggle",
        endpoint=toggle_tool,
        methods=["POST", "GET"],
    ),
    Route("/servers/{server_id}/delete", endpoint=delete_server, methods=["POST"]),
]

