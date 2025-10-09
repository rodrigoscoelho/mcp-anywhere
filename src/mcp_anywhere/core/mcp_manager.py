"""MCP Manager for handling dynamic server mounting and unmounting."""

import asyncio
import re
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from mcp_anywhere.container.manager import ContainerManager
from mcp_anywhere.database import MCPServer
from mcp_anywhere.logging_config import get_logger
from mcp_anywhere.security.file_manager import SecureFileManager

logger = get_logger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 20.0
TOOL_METADATA_TIMEOUT_SECONDS = 10.0


def create_mcp_config(server: "MCPServer") -> dict[str, dict[str, Any]]:
    """Create MCP proxy configuration for both new and existing containers.

    Args:
        server: Single MCPServer instance from database

    Returns:
        Dict containing both 'new' and 'existing' configuration options
    """
    container_manager = ContainerManager()

    if server.runtime_type == "docker":
        start_command = (server.start_command or "").strip()
        if not start_command:
            logger.warning(f"No start command for server {server.name}")
            return {"new": {}, "existing": {}}

        env_vars = container_manager._get_env_vars(server)
        if "MCP_TRANSPORT" not in env_vars:
            env_vars["MCP_TRANSPORT"] = "stdio"

        docker_config = {
            "command": "sh",
            "args": ["-lc", start_command],
            "env": env_vars,
            "transport": env_vars.get("MCP_TRANSPORT", "stdio"),
            "init_timeout": 30,
        }

        return {"new": docker_config, "existing": {}}

    # Use container manager's parsing logic for commands
    run_command = container_manager._parse_start_command(server)

    if not run_command:
        logger.warning(f"No start command for server {server.name}")
        return {"new": {}, "existing": {}}

    # Configuration for existing container (docker exec)
    container_name = container_manager._get_container_name(
        server.id, server.name
    )
    existing_config = {
        "command": "docker",
        "args": [
            "exec",
            "-i",  # Interactive (for stdio)
            container_name,  # Connect to existing container
            *run_command,  # The actual MCP command
        ],
        "env": {},
        "transport": "stdio",
    }

    # Configuration for new container (docker run)
    image_tag = container_manager.get_image_tag(server)

    # Extract environment variables (both regular and secret file paths)
    env_vars = container_manager._get_env_vars(server)
    if "MCP_TRANSPORT" not in env_vars:
        env_vars["MCP_TRANSPORT"] = "stdio"
    env_args = []
    for key, value in env_vars.items():
        env_args.extend(["-e", f"{key}={value}"])

    # Prepare secret file volume mounts
    volume_args = []
    secret_files = getattr(server, "secret_files", [])

    if len(secret_files) > 0:
        file_manager = SecureFileManager()
        container_files = file_manager.prepare_container_files(server.id, secret_files)

        for host_path, container_path in container_files.items():
            volume_args.extend(["-v", f"{host_path}:{container_path}:ro"])

    new_config = {
        "command": "docker",
        "args": [
            "run",
            # "--rm",  # Do not remove container immediately on exit
            "-i",  # Interactive (for stdio)
            "--name",
            container_name,  # Container name
            "--memory",
            "512m",  # Memory limit
            "--cpus",
            "0.5",  # CPU limit
            *env_args,  # Environment variables
            *volume_args,  # Secret file volume mounts
            image_tag,  # Our pre-built image
            *run_command,  # The actual MCP command
        ],
        "env": {},  # Already passed via docker -e
        "transport": "stdio",
        "init_timeout": 15,  # Add a 15-second initialization timeout
    }

    return {"new": new_config, "existing": existing_config}


class MCPManager:
    """Manages the MCP Anywhere router and handles runtime server mounting/unmounting.

    This class encapsulates the FastMCP router and provides methods to dynamically
    add and remove MCP servers at runtime using FastMCP's mount() capability.
    """

    def __init__(self, router: FastMCP) -> None:
        """Initialize the MCP manager with a router."""
        self.router = router
        self.mounted_servers: dict[str, FastMCP] = {}
        self.mounted_server_names: dict[str, str] = {}
        self._http_client = None  # Cache HTTP client for internal calls
        logger.info("Initialized MCPManager")

    @staticmethod
    def _format_prefix(server_name: str, fallback: str) -> str:
        """Convert a server name into a safe prefix for mounted tools."""

        sanitized = re.sub(r"\s+", "_", server_name.strip())
        sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", sanitized)
        sanitized = sanitized.strip("_.")

        return sanitized or fallback

    async def add_server(self, server_config: "MCPServer") -> list[dict[str, Any]]:
        """Add a new MCP server dynamically using FastMCP's mount capability.

        Args:
            server_config: The MCPServer database model

        Returns:
            List of discovered tools from the server
        """
        # Get both configuration options
        config_options = create_mcp_config(server_config)

        if not config_options["new"] and not config_options["existing"]:
            raise RuntimeError(
                f"Failed to create proxy config for {server_config.name}"
            )

        # Check container health and select appropriate config
        container_manager = ContainerManager()
        if container_manager._is_container_healthy(server_config):
            server_config_dict = config_options["existing"]
            logger.debug(f"Using existing container for {server_config.name}")
        else:
            server_config_dict = config_options["new"]
            logger.debug(f"Using new container for {server_config.name}")

        # Create proxy configuration in expected format
        proxy_config = {"mcpServers": {server_config.name: server_config_dict}}

        # Create FastMCP proxy for the server
        proxy = FastMCP.as_proxy(proxy_config)

        # Mount with a human-readable prefix derived from the server name
        prefix = self._format_prefix(server_config.name, server_config.id)
        self.router.mount(proxy, prefix=prefix)

        # Track the mounted server
        self.mounted_servers[server_config.id] = proxy
        self.mounted_server_names[server_config.id] = server_config.name

        logger.info(
            f"Successfully mounted server '{server_config.name}' with prefix '{prefix}'"
        )

        # Discover and return tools for existing containers
        return await self._discover_server_tools(server_config.id)

    def remove_server(self, server_id: str) -> None:
        """Remove an MCP server dynamically by unmounting it from all managers."""
        if server_id not in self.mounted_servers:
            logger.warning(f"Server '{server_id}' not found in mounted servers")
            return

        try:
            # Get the mounted server proxy
            mounted_server = self.mounted_servers[server_id]

            # Remove from all FastMCP internal managers
            # Based on FastMCP developer's example in issue #934
            for manager in [
                self.router._tool_manager,
                self.router._resource_manager,
                self.router._prompt_manager,
            ]:
                # Find and remove the mount for this server
                mounts_to_remove = [
                    m for m in manager._mounted_servers if m.server is mounted_server
                ]
                for mount in mounts_to_remove:
                    manager._mounted_servers.remove(mount)
                    logger.debug(f"Removed mount from {manager.__class__.__name__}")

            # FastMCP handles cache management internally

            # Remove from our tracking
            del self.mounted_servers[server_id]
            self.mounted_server_names.pop(server_id, None)

            logger.info(
                f"Successfully unmounted server '{server_id}' from all managers"
            )

        except (RuntimeError, ValueError, KeyError) as e:
            logger.exception(f"Failed to remove server '{server_id}': {e}")
            raise

    async def _discover_server_tools(self, server_id: str) -> list[dict[str, Any]]:
        """Discover tools from a mounted server.

        Args:
            server_id: The ID of the server to discover tools from

        Returns:
            List of discovered tools with name and description
        """
        if server_id not in self.mounted_servers:
            return []

        try:
            tools = await asyncio.wait_for(
                self.mounted_servers[server_id]._tool_manager.get_tools(),
                timeout=DISCOVERY_TIMEOUT_SECONDS,
            )

            # Convert tools to the format expected by the database
            discovered_tools = []
            for key, tool in tools.items():
                schema = getattr(tool, "parameters", None)
                if not isinstance(schema, dict):
                    schema = None

                discovered_tools.append(
                    {
                        "name": key,
                        "description": tool.description or "",
                        "schema": schema,
                    }
                )

            logger.info(
                f"Discovered {len(discovered_tools)} tools for server '{server_id}'"
            )
            return discovered_tools

        except (
            RuntimeError,
            ValueError,
            ConnectionError,
            AttributeError,
            asyncio.TimeoutError,
        ) as e:
            if isinstance(e, asyncio.TimeoutError):
                logger.error(f"Tool discovery timed out for server '{server_id}' after {DISCOVERY_TIMEOUT_SECONDS:.1f}s")
                wrapped_error: Exception = RuntimeError(
                    "Tempo limite ao descobrir ferramentas do servidor MCP."
                )
            else:
                logger.error(
                    f"Failed to discover tools for server '{server_id}': {e}"
                )
                wrapped_error = e

            # Check container logs for startup errors
            container_manager = ContainerManager()
            error_logs = container_manager.get_container_error_logs(
                server_id, server_name=self.mounted_server_names.get(server_id)
            )

            if error_logs:
                # Try to extract a meaningful error message
                error_msg = container_manager._extract_error_from_logs(error_logs)
                if error_msg:
                    logger.error(
                        f"Container startup error for server '{server_id}': {error_msg}"
                    )
                    # Re-raise with the more meaningful error message
                    raise RuntimeError(
                        f"Server startup failed: {error_msg}"
                    ) from e

            # Re-raise the original error (or wrapped timeout error) if no better error found
            if wrapped_error is e:
                raise
            raise wrapped_error from e

    def is_server_mounted(self, server_id: str) -> bool:
        """Return True if the server is currently mounted in FastMCP."""

        return server_id in self.mounted_servers

    async def get_runtime_tool(self, tool_key: str):
        """Retrieve a runtime tool by its prefixed key."""

        try:
            return await asyncio.wait_for(
                self.router._tool_manager.get_tool(tool_key),
                timeout=TOOL_METADATA_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                (
                    "Tempo limite ao recuperar metadados da ferramenta "
                    f"'{tool_key}' após {TOOL_METADATA_TIMEOUT_SECONDS:.1f}s."
                )
            ) from exc

    async def call_tool_via_http(self, tool_key: str, arguments: dict[str, Any], app):
        """Execute a tool via HTTP request to FastMCP app to ensure context is established.
        
        Args:
            tool_key: The tool key to execute
            arguments: Tool arguments
            app: The Starlette app containing the FastMCP HTTP app
            
        Returns:
            Tool execution result
        """
        
        logger.debug(f"DEBUG: Executando ferramenta via HTTP para garantir contexto: {tool_key}")
        
        # Create HTTP client with ASGI transport
        if self._http_client is None:
            from httpx import ASGITransport, AsyncClient
            self._http_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        
        # Make HTTP request to FastMCP endpoint
        try:
            # FastMCP HTTP API endpoint for tool calls
            mcp_path = "/mcp/"  # FastMCP mounts at /mcp/ path
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": tool_key,
                    "arguments": arguments
                }
            }
            
            logger.debug(f"DEBUG: Fazendo requisição HTTP para {mcp_path} com payload: {payload}")

            # Prepare headers including any persistent mcp session id
            headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
            if getattr(self, "_mcp_session_id", None):
                headers["mcp-session-id"] = self._mcp_session_id

            # Try at most twice: first attempt may return a session id we must echo back
            max_attempts = 2
            attempt = 0
            while attempt < max_attempts:
                response = await self._http_client.post(
                    mcp_path,
                    json=payload,
                    headers=headers
                )

                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    if "application/json" in content_type:
                        result = response.json()
                        logger.debug(f"DEBUG: Chamada HTTP bem sucedida: {result}")
                        return result.get("result", result)
                    if "text/event-stream" in content_type:
                        # Parse SSE stream to extract the result
                        stream_text = response.text
                        logger.debug(f"DEBUG: Chamada HTTP retornou SSE stream, parseando...")
                        logger.debug(f"DEBUG: SSE stream completo (primeiros 500 chars): {stream_text[:500]}")

                        # Parse SSE format: lines starting with "data: " contain JSON
                        import json as json_module
                        result_data = None
                        error_data = None
                        for line in stream_text.split('\n'):
                            line = line.strip()
                            if line.startswith('data: '):
                                try:
                                    data_json = line[6:]  # Remove "data: " prefix
                                    logger.debug(f"DEBUG: Tentando parsear data line: {data_json[:200]}")
                                    parsed = json_module.loads(data_json)
                                    # Look for the result or error in the parsed data
                                    if isinstance(parsed, dict):
                                        if "error" in parsed:
                                            error_data = parsed["error"]
                                            logger.error(f"DEBUG: Erro retornado pela ferramenta: {error_data}")
                                            # Raise ToolError with the error message
                                            error_msg = error_data.get("message", "Unknown error")
                                            error_details = error_data.get("data", "")
                                            full_msg = f"{error_msg}: {error_details}" if error_details else error_msg
                                            raise ToolError(full_msg)
                                        elif "result" in parsed:
                                            result_data = parsed["result"]
                                            logger.debug(f"DEBUG: Encontrado result no SSE: {result_data}")
                                        elif "content" in parsed:
                                            result_data = parsed
                                            logger.debug(f"DEBUG: Encontrado content no SSE: {result_data}")
                                except ToolError:
                                    # Re-raise ToolError to be handled by the caller
                                    raise
                                except (json_module.JSONDecodeError, ValueError) as e:
                                    logger.debug(f"DEBUG: Erro ao parsear linha SSE: {line[:100]}... - {e}")
                                    continue

                        if result_data is not None:
                            logger.debug(f"DEBUG: SSE parseado com sucesso: {result_data}")
                            return result_data
                        else:
                            logger.warning(f"DEBUG: Não foi possível extrair resultado do SSE stream: {stream_text[:200]}...")
                            return {"stream": stream_text}

                    # Unknown content type: return raw text
                    raw_text = response.text
                    logger.debug(f"DEBUG: Chamada HTTP retornou content-type={content_type}; returning raw text")
                    return raw_text

                # If server returned a session id header, retry with it
                session_id = response.headers.get("mcp-session-id")
                if session_id and "mcp-session-id" not in headers:
                    logger.debug(f"DEBUG: Server requested mcp-session-id={session_id}; retrying once with this header")
                    self._mcp_session_id = session_id
                    headers["mcp-session-id"] = session_id
                    attempt += 1
                    continue

                # Non-retriable failure; log and break to fallback
                resp_text = response.text if hasattr(response, "text") else "<no response body>"
                logger.error(f"DEBUG: Falha na chamada HTTP: status={response.status_code}, response='{resp_text}', url='{mcp_path}'")
                break

            # Fallback to direct call (no app) to avoid recursion
            return await self.call_tool(tool_key, arguments, None)

        except ToolError:
            # Re-raise ToolError - this is a legitimate tool error, not a communication error
            raise
        except Exception as e:
            logger.error(f"DEBUG: Erro na chamada HTTP: {e}")
            # Fallback to direct call
            return await self.call_tool(tool_key, arguments, None)

    async def call_tool(self, tool_key: str, arguments: dict[str, Any], app=None):
        """Execute a tool via the FastMCP router.

        Always tries to execute via HTTP request to the FastMCP endpoint when app is provided,
        as this ensures proper context establishment. Falls back to direct calls if HTTP fails.
        """
        logger.debug(f"DEBUG: call_tool chamado - tool_key={tool_key}, arguments={arguments}, app_provided={bool(app)}")

        # Always try HTTP path first when app is available (recommended approach)
        if app is not None:
            try:
                logger.debug("DEBUG: Executando ferramenta via HTTP (recomendado)")
                return await self.call_tool_via_http(tool_key, arguments, app)
            except Exception as http_exc:
                logger.warning(f"DEBUG: Falha na chamada HTTP: {http_exc}. Tentando fallback direto.")

        # Fallback: try to use the FastMCP context if available
        try:
            from fastmcp.server.dependencies import get_context
            context = get_context()
            logger.debug("DEBUG: Contexto FastMCP encontrado, executando diretamente")
            return await self.router._tool_manager.call_tool(tool_key, arguments)
        except RuntimeError as ctx_err:
            logger.warning(f"DEBUG: Contexto FastMCP não disponível: {ctx_err}. Tentando chamada direta sem contexto.")
            # Context not available; try direct call as last resort
            try:
                return await self.router._tool_manager.call_tool(tool_key, arguments)
            except Exception as direct_err:
                logger.error(f"DEBUG: Falha na chamada direta ao tool_manager: {direct_err}")
                raise
        except Exception as e:
            logger.debug(f"DEBUG: Erro inesperado ao verificar contexto: {e}")
            # Final fallback: direct call to the tool manager
            try:
                return await self.router._tool_manager.call_tool(tool_key, arguments)
            except Exception as direct_err:
                logger.error(f"DEBUG: Falha na chamada direta ao tool_manager (final): {direct_err}")
                raise
