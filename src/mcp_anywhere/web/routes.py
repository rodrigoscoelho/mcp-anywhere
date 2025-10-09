import json
from time import perf_counter
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

from fastmcp.exceptions import NotFoundError, ToolError
from pydantic_core import to_jsonable_python

from mcp_anywhere.claude_analyzer import AsyncClaudeAnalyzer
from mcp_anywhere.container.manager import ContainerManager
from mcp_anywhere.core.mcp_manager import MCPManager
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


def _normalize_schema_type(raw_type: Any) -> str:
    """Return the first valid JSON schema type from a raw type declaration."""

    if isinstance(raw_type, list):
        for option in raw_type:
            if isinstance(option, str):
                return option
        return "string"
    if isinstance(raw_type, str):
        return raw_type
    return "string"


def _build_tool_form_fields(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Build metadata to render a tool testing form from a JSON schema."""

    if not isinstance(schema, dict):
        return {"fields": [], "use_raw_json": True, "raw_examples": []}

    schema_examples = (
        schema.get("examples")
        if isinstance(schema.get("examples"), list)
        else []
    )

    schema_type = _normalize_schema_type(schema.get("type"))
    if schema_type != "object":
        return {
            "fields": [],
            "use_raw_json": True,
            "raw_examples": schema_examples,
        }

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {
            "fields": [],
            "use_raw_json": True,
            "raw_examples": schema_examples,
        }

    required_fields = set(schema.get("required") or [])
    fields: list[dict[str, Any]] = []

    for name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue

        json_type = _normalize_schema_type(field_schema.get("type"))
        enum_options_raw = field_schema.get("enum")
        enum_options: list[dict[str, Any]] = []
        if isinstance(enum_options_raw, list) and enum_options_raw:
            for option in enum_options_raw:
                try:
                    serialized = json.dumps(option, ensure_ascii=False)
                except (TypeError, ValueError):
                    serialized = json.dumps(str(option), ensure_ascii=False)
                enum_options.append(
                    {
                        "value": serialized,
                        "label": str(option),
                    }
                )

        examples = field_schema.get("examples")
        placeholder = None
        if isinstance(examples, list) and examples:
            placeholder = str(examples[0])
        elif isinstance(field_schema.get("example"), (str, int, float)):
            placeholder = str(field_schema["example"])

        default_value = field_schema.get("default")
        default_serialized = None
        if enum_options and default_value is not None:
            try:
                default_serialized = json.dumps(default_value, ensure_ascii=False)
            except (TypeError, ValueError):
                default_serialized = json.dumps(str(default_value), ensure_ascii=False)

        widget = "text"
        if enum_options:
            widget = "select"
        elif json_type in {"integer", "number"}:
            widget = "number"
        elif json_type == "boolean":
            widget = "checkbox"
        elif json_type in {"object", "array"}:
            widget = "json"

        fields.append(
            {
                "name": name,
                "label": field_schema.get("title") or name.replace("_", " ").title(),
                "description": field_schema.get("description"),
                "required": name in required_fields,
                "json_type": json_type,
                "enum": enum_options,
                "widget": widget,
                "default": default_value,
                "default_serialized": default_serialized,
                "placeholder": placeholder,
                "format": field_schema.get("format"),
            }
        )

    return {
        "fields": fields,
        "use_raw_json": False,
        "raw_examples": schema_examples,
    }


def _parse_tool_form_data(
    form: FormData,
    fields: list[dict[str, Any]],
    use_raw_json: bool,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Parse posted form data into tool arguments using schema metadata."""

    errors: dict[str, str] = {}

    if use_raw_json:
        raw_payload = (_as_optional_str(form.get("__raw_payload")) or "").strip()
        if not raw_payload:
            errors["__raw_payload"] = "Informe um objeto JSON com os argumentos da ferramenta."
            return {}, errors
        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            errors["__raw_payload"] = f"JSON inválido: {exc.msg}"
            return {}, errors
        if not isinstance(parsed, dict):
            errors["__raw_payload"] = "O JSON deve representar um objeto de argumentos."
            return {}, errors
        return parsed, errors

    arguments: dict[str, Any] = {}

    for field in fields:
        name = field["name"]
        widget = field["widget"]
        raw_value = form.get(name)

        if widget == "checkbox":
            value = False if raw_value is None else str(raw_value).lower() not in {"false", "0", "off"}
            arguments[name] = value
            continue

        if widget == "json":
            text_value = (_as_optional_str(raw_value) or "").strip()
            if not text_value:
                if field.get("required"):
                    errors[name] = "Este campo é obrigatório."
                continue
            try:
                arguments[name] = json.loads(text_value)
            except json.JSONDecodeError as exc:
                errors[name] = f"JSON inválido: {exc.msg}"
            continue

        if widget == "number":
            text_value = (_as_optional_str(raw_value) or "").strip()
            if not text_value:
                if field.get("required"):
                    errors[name] = "Este campo é obrigatório."
                continue
            try:
                if field.get("json_type") == "integer":
                    arguments[name] = int(text_value)
                else:
                    arguments[name] = float(text_value)
            except ValueError:
                errors[name] = "Informe um número válido."
            continue

        if widget == "select":
            text_value = (_as_optional_str(raw_value) or "").strip()
            if not text_value:
                if field.get("required"):
                    errors[name] = "Este campo é obrigatório."
                continue
            try:
                arguments[name] = json.loads(text_value)
            except json.JSONDecodeError:
                # Fallback to raw string if json decoding fails
                arguments[name] = text_value
            continue

        # Default: treat as simple text input
        text_value = (_as_optional_str(raw_value) or "").strip()
        if not text_value:
            if field.get("required"):
                errors[name] = "Este campo é obrigatório."
            continue
        arguments[name] = text_value

    return arguments, errors


def _render_tool_test_result(
    request: Request,
    *,
    tool: MCPServerTool | None,
    tool_name: str | None,
    status: str,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    """Render the HTMX partial for tool testing feedback."""

    payload = get_template_context(
        request,
        tool=tool,
        tool_name=tool.tool_name if tool else tool_name,
        status=status,
        **context,
    )
    return templates.TemplateResponse(
        request,
        "partials/tool_test_result.html",
        payload,
        status_code=status_code,
    )


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
        "runtime_type": "uvx",
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


def _candidate_tool_key_fragments(tool_key: str) -> set[str]:
    """Generate possible suffixes for matching runtime tool keys."""
    fragments: set[str] = set()
    if not tool_key:
        return fragments

    fragments.add(tool_key)

    if "/" not in tool_key:
        fragments.add(tool_key.rsplit("_", 1)[-1])
        fragments.add(tool_key.rsplit(".", 1)[-1])
        return {frag for frag in fragments if frag}

    prefix, remainder = tool_key.split("/", 1)
    if remainder:
        fragments.add(remainder)
        fragments.add(remainder.rsplit("/", 1)[-1])

    if prefix:
        if "." in prefix:
            fragments.add(f"{prefix.split('.', 1)[-1]}/{remainder}")
        if "_" in prefix:
            fragments.add(f"{prefix.split('_', 1)[-1]}/{remainder}")

    return {frag for frag in fragments if frag}


async def _refresh_runtime_tool_registration(
    db_session,
    mcp_manager: MCPManager,
    server: MCPServer,
    tool: MCPServerTool,
):
    """
    Attempt to resolve runtime tool metadata when stored key is stale.

    Returns a tuple of (runtime_tool, updated_flag).
    """
    mounted_proxy = mcp_manager.mounted_servers.get(server.id) if mcp_manager else None
    if not mounted_proxy:
        return None, False

    try:
        runtime_tools = await mounted_proxy._tool_manager.get_tools()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.debug(
            "Failed to refresh runtime tools for server %s: %s", server.id, exc
        )
        return None, False

    fragments = _candidate_tool_key_fragments(tool.tool_name)
    description = (tool.tool_description or "").strip()

    selected_tool = None
    for runtime_tool in runtime_tools.values():
        runtime_key = getattr(runtime_tool, "key", None)
        runtime_name = getattr(runtime_tool, "name", None)
        runtime_description = (getattr(runtime_tool, "description", "") or "").strip()

        if runtime_key == tool.tool_name:
            selected_tool = runtime_tool
            break

        if runtime_key and runtime_key in fragments:
            selected_tool = runtime_tool
            break

        if runtime_key and any(
            runtime_key.endswith(f"/{frag}") for frag in fragments if "/" not in frag
        ):
            selected_tool = runtime_tool
            break

        if runtime_name and runtime_name in fragments:
            selected_tool = runtime_tool
            break

        if description and runtime_description == description:
            selected_tool = runtime_tool
            break

    if not selected_tool:
        return None, False

    if getattr(selected_tool, "key", tool.tool_name) != tool.tool_name:
        old_key = tool.tool_name
        tool.tool_name = selected_tool.key
        db_session.add(tool)
        logger.info(
            "Resynchronized runtime tool key for server %s: %s -> %s",
            server.id,
            old_key,
            selected_tool.key,
        )
        return selected_tool, True

    return selected_tool, False


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

            tool_test_info: dict[str, dict[str, Any]] = {}
            mcp_manager = get_mcp_manager(request)
            server_mounted = bool(
                mcp_manager and mcp_manager.is_server_mounted(server.id)
            )
            tools_updated = False

            for tool in tools:
                info: dict[str, Any] = {
                    "available": False,
                    "error": None,
                    "fields": [],
                    "schema": None,
                    "use_raw_json": False,
                    "raw_examples": [],
                }

                stored_schema = (
                    tool.tool_schema if isinstance(tool.tool_schema, dict) else None
                )
                if stored_schema:
                    schema_info = _build_tool_form_fields(stored_schema)
                    info["schema"] = stored_schema
                    info["fields"] = schema_info.get("fields", [])
                    info["use_raw_json"] = bool(schema_info.get("use_raw_json"))
                    info["raw_examples"] = schema_info.get("raw_examples", [])

                runtime_tool = None
                runtime_ready = True
                if not tool.is_enabled:
                    info["error"] = "Ferramenta desabilitada. Ative-a para realizar testes."
                    runtime_ready = False
                elif not server.is_active:
                    info["error"] = "Servidor inativo. Ative o servidor para testar as ferramentas."
                    runtime_ready = False
                elif not mcp_manager:
                    info["error"] = "Gerenciador MCP indisponível neste modo de execução."
                    runtime_ready = False
                elif not server_mounted:
                    info["error"] = "Servidor ainda não está montado ou em execução."
                    runtime_ready = False

                if runtime_ready and mcp_manager:
                    try:
                        runtime_tool = await mcp_manager.get_runtime_tool(tool.tool_name)
                    except NotFoundError:
                        runtime_tool, updated = await _refresh_runtime_tool_registration(
                            db_session, mcp_manager, server, tool
                        )
                        tools_updated = tools_updated or updated
                        if not runtime_tool:
                            info["error"] = (
                                "Ferramenta não encontrada no runtime atual. Refaça o build do servidor."
                            )
                    except TimeoutError:
                        info["error"] = (
                            "Tempo limite ao carregar as informações da ferramenta."
                            " Utilizando os dados já armazenados."
                        )
                    except Exception as exc:  # pragma: no cover - logging defensivo
                        logger.debug(
                            "Falha ao carregar metadados da ferramenta %s do servidor %s: %s",
                            tool.tool_name,
                            server.id,
                            exc,
                        )
                        info["error"] = "Não foi possível carregar o esquema da ferramenta."

                if runtime_tool:
                    schema = getattr(runtime_tool, "parameters", None)
                    schema_dict = schema if isinstance(schema, dict) else {}
                    schema_info = _build_tool_form_fields(schema_dict)
                    info["schema"] = schema_dict
                    info["fields"] = schema_info.get("fields", [])
                    info["use_raw_json"] = bool(schema_info.get("use_raw_json"))
                    info["raw_examples"] = schema_info.get("raw_examples", [])
                    info["available"] = True
                    info["error"] = None
                    if schema_dict and schema_dict != (tool.tool_schema or {}):
                        tool.tool_schema = schema_dict
                        tools_updated = True

                tool_test_info[tool.id] = info

            if tools_updated:
                await db_session.commit()

            tool_tests_available = any(
                details.get("available") for details in tool_test_info.values()
            )

            return templates.TemplateResponse(
                request,
                "servers/detail.html",
                get_template_context(
                    request,
                    server=server,
                    tools=tools,
                    tool_test_info=tool_test_info,
                    tool_tests_available=tool_tests_available,
                ),
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
                if server.runtime_type == "docker":
                    server.build_logs = (
                        "Docker runtime ready (no managed image build required)"
                    )
                else:
                    server.build_logs = f"Successfully built image {image_tag}"
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
                    if container_manager._manages_container(server):
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



async def test_tool(request: Request) -> HTMLResponse:
    """Executa uma ferramenta MCP manualmente e retorna o resultado renderizado."""

    server_id = request.path_params["server_id"]
    tool_id = request.path_params["tool_id"]

    async with get_async_session() as db_session:
        stmt = (
            select(MCPServerTool)
            .options(selectinload(MCPServerTool.server))
            .where(
                MCPServerTool.id == tool_id,
                MCPServerTool.server_id == server_id,
            )
        )
        result = await db_session.execute(stmt)
        tool = result.scalar_one_or_none()
        server = tool.server if tool else None

        if not tool or not server:
            return _render_tool_test_result(
                request,
                tool=None,
                tool_name=None,
                status="error",
                status_code=404,
                error_message="Ferramenta não encontrada para este servidor.",
            )

        if not server.is_active:
            return _render_tool_test_result(
                request,
                tool=tool,
                tool_name=tool.tool_name,
                status="error",
                status_code=400,
                error_message="Servidor inativo. Ative-o para executar ferramentas.",
            )

        if not tool.is_enabled:
            return _render_tool_test_result(
                request,
                tool=tool,
                tool_name=tool.tool_name,
                status="error",
                status_code=400,
                error_message="Ferramenta desabilitada. Ative-a para realizar testes.",
            )

        mcp_manager = get_mcp_manager(request)
        if not mcp_manager:
            return _render_tool_test_result(
                request,
                tool=tool,
                tool_name=tool.tool_name,
                status="error",
                status_code=503,
                error_message="Gerenciador MCP indisponível no momento.",
            )

        if not mcp_manager.is_server_mounted(server.id):
            return _render_tool_test_result(
                request,
                tool=tool,
                tool_name=tool.tool_name,
                status="error",
                status_code=503,
                error_message="Servidor ainda não está montado. Aguarde a inicialização ou refaça o build.",
            )

        tool_metadata_updated = False

        try:
            runtime_tool = await mcp_manager.get_runtime_tool(tool.tool_name)
        except NotFoundError:
            runtime_tool, updated = await _refresh_runtime_tool_registration(
                db_session, mcp_manager, server, tool
            )
            tool_metadata_updated = tool_metadata_updated or updated
            if not runtime_tool:
                if tool_metadata_updated:
                    await db_session.commit()
                return _render_tool_test_result(
                    request,
                    tool=tool,
                    tool_name=tool.tool_name,
                    status="error",
                    status_code=404,
                    error_message="Ferramenta não está disponível no runtime atual.",
                )
        except TimeoutError:
            return _render_tool_test_result(
                request,
                tool=tool,
                tool_name=tool.tool_name,
                status="error",
                status_code=504,
                error_message=(
                    "Tempo limite ao recuperar metadados da ferramenta. Tente novamente mais tarde."
                ),
            )
        except Exception as exc:  # pragma: no cover - logging defensivo
            logger.exception(
                "Erro ao obter metadados da ferramenta %s (%s)", tool.tool_name, tool.id
            )
            return _render_tool_test_result(
                request,
                tool=tool,
                tool_name=tool.tool_name,
                status="error",
                status_code=500,
                error_message="Falha ao carregar a ferramenta a partir do runtime.",
                error_details=str(exc),
            )

        schema = getattr(runtime_tool, "parameters", None)
        schema_dict = schema if isinstance(schema, dict) else {}
        schema_info = _build_tool_form_fields(schema_dict)
        use_raw_json = bool(schema_info.get("use_raw_json"))
        form_fields = schema_info.get("fields", [])

        if schema_dict and schema_dict != (tool.tool_schema or {}):
            tool.tool_schema = schema_dict
            tool_metadata_updated = True

        form_data = await request.form()
        arguments, validation_errors = _parse_tool_form_data(
            form_data, form_fields, use_raw_json
        )

        if validation_errors:
            if tool_metadata_updated:
                await db_session.commit()
            return _render_tool_test_result(
                request,
                tool=tool,
                tool_name=tool.tool_name,
                status="error",
                status_code=400,
                error_message="Corrija os campos destacados antes de executar a ferramenta.",
                validation_errors=validation_errors,
            )

        start = perf_counter()
        try:
            # Prefer to call the runtime-registered key if available (handles stored suffixes)
            call_key = getattr(runtime_tool, "key", tool.tool_name) if runtime_tool is not None else tool.tool_name
            try:
                tool_result = await mcp_manager.call_tool(call_key, arguments)
            except NotFoundError:
                # The router reports the key missing; attempt to resolve by enumerating runtime tools
                try:
                    runtime_tools = await mcp_manager.router._tool_manager.get_tools()
                except Exception:
                    # Re-raise original NotFoundError if we cannot query runtime tools
                    raise

                fragments = _candidate_tool_key_fragments(tool.tool_name)
                matched_key = None
                for runtime_key in runtime_tools.keys():
                    if runtime_key == tool.tool_name:
                        matched_key = runtime_key
                        break
                    if runtime_key in fragments:
                        matched_key = runtime_key
                        break
                    if any(runtime_key.endswith(f"/{frag}") for frag in fragments if "/" not in frag):
                        matched_key = runtime_key
                        break

                if matched_key:
                    # Update stored tool key to the resolved runtime key so subsequent calls succeed
                    try:
                        tool.tool_name = matched_key
                        db_session.add(tool)
                        tool_metadata_updated = True
                        await db_session.commit()
                    except Exception:
                        # Best-effort update; continue even if DB commit fails
                        pass

                    tool_result = await mcp_manager.call_tool(matched_key, arguments)
                else:
                    # No matching runtime key found: log and show runtime keys to help debugging
                    available = list(runtime_tools.keys())
                    logger.debug(
                        "Tool %s not found when calling key %s. Runtime keys: %s; fragments: %s",
                        tool.tool_name,
                        call_key,
                        available,
                        fragments,
                    )
                    return _render_tool_test_result(
                        request,
                        tool=tool,
                        tool_name=tool.tool_name,
                        status="error",
                        status_code=404,
                        error_message=(
                            "Ferramenta não encontrada no runtime atual. "
                            "Chaves disponíveis: %s. Refaça o build do servidor se necessário."
                            % (", ".join(available) if available else "(nenhuma)")
                        ),
                        error_details=(
                            "Tente sincronizar as chaves do runtime ou verifique o prefixo usado ao montar o servidor."
                        ),
                    )

        except ToolError as exc:
            if tool_metadata_updated:
                await db_session.commit()
            logger.info(
                "Execução da ferramenta %s retornou erro: %s",
                tool.tool_name,
                exc,
            )
            return _render_tool_test_result(
                request,
                tool=tool,
                tool_name=tool.tool_name,
                status="error",
                status_code=400,
                error_message=str(exc),
                arguments_json=json.dumps(
                    to_jsonable_python(arguments), ensure_ascii=False, indent=2
                ),
            )
        except Exception as exc:  # pragma: no cover - logging defensivo
            if tool_metadata_updated:
                await db_session.commit()
            logger.exception(
                "Falha ao executar ferramenta %s (%s)", tool.tool_name, tool.id
            )
            return _render_tool_test_result(
                request,
                tool=tool,
                tool_name=tool.tool_name,
                status="error",
                status_code=500,
                error_message="Erro inesperado ao executar a ferramenta.",
                error_details=str(exc),
            )

        duration_ms = (perf_counter() - start) * 1000

        content_json = json.dumps(
            to_jsonable_python(tool_result.content), ensure_ascii=False, indent=2
        )
        structured_json = None
        if tool_result.structured_content is not None:
            structured_json = json.dumps(
                to_jsonable_python(tool_result.structured_content),
                ensure_ascii=False,
                indent=2,
            )

        arguments_json = json.dumps(
            to_jsonable_python(arguments), ensure_ascii=False, indent=2
        )

        if tool_metadata_updated:
            await db_session.commit()

        return _render_tool_test_result(
            request,
            tool=tool,
            tool_name=tool.tool_name,
            status="success",
            arguments_json=arguments_json,
            result_content_json=content_json,
            structured_json=structured_json,
            duration_ms=duration_ms,
        )

async def handle_claude_connection_error(
    request: Request, github_url: str, error: ConnectionError
) -> HTMLResponse:
    """Handle Claude analysis connection failures with fallback."""
    logger.warning(f"Claude analysis failed for {github_url}: {error}")

    analysis = {
        "name": "analyzed-server",
        "description": "Claude analysis unavailable - please fill manually",
        "runtime_type": "uvx",
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
                if server.runtime_type == "docker":
                    server.build_logs = (
                        "Docker runtime ready (no managed image build required)"
                    )
                else:
                    server.build_logs = f"Successfully built image {image_tag}"
                await db_session.commit()

                # Refresh server with eager loading of relationships
                await db_session.refresh(server, ["secret_files"])

                # Add server to MCP manager and get tools
                mcp_manager = get_mcp_manager(request)
                if mcp_manager:
                    # Clean up any existing container before adding
                    if container_manager._manages_container(server):
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
            if server.runtime_type == "docker":
                server.build_logs = (
                    "Docker runtime ready (no managed image build required)"
                )
            else:
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

                if container_manager._manages_container(server):
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
    Route(
        "/servers/{server_id}/tools/{tool_id}/test",
        endpoint=test_tool,
        methods=["POST"],
    ),
    Route("/servers/{server_id}/delete", endpoint=delete_server, methods=["POST"]),
]

