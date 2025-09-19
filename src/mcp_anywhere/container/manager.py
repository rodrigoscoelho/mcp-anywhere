"""Manages container lifecycle for MCP servers in any language.

Supports:
- npx: Node.js/JavaScript servers
- uvx: Python servers
"""

import json
import os
import re
import shlex
from typing import Any, Optional, Protocol, cast

import docker
from docker import DockerClient
from docker.errors import APIError, ImageNotFound, NotFound
from llm_sandbox import SandboxSession
from sqlalchemy import select

from mcp_anywhere.config import Config
from mcp_anywhere.database import (
    MCPServer,
    get_active_servers,
    get_async_session,
    get_built_servers,
)
from mcp_anywhere.database_utils import store_server_tools
from mcp_anywhere.logging_config import get_logger
from mcp_anywhere.security.file_manager import SecureFileManager

logger = get_logger(__name__)


class DockerClientProtocol(Protocol):
    def ping(self) -> Any:
        ...

    def close(self) -> Any:
        ...

    @property
    def containers(self) -> Any:
        ...

    @property
    def images(self) -> Any:
        ...


class _DummyContainers:
    def get(self, name: str) -> Any:
        raise NotFound(f"Docker not available (requested container: {name})")


class _DummyImages:
    def get(self, name: str) -> Any:
        raise ImageNotFound(name)

    def pull(self, name: str) -> Any:
        raise APIError(f"Docker not available (pull attempted: {name})")


class _NoDockerClient:
    def ping(self) -> None:
        raise ConnectionError("Docker client not available")

    def close(self) -> None:
        logger.debug("No-op close on dummy Docker client")

    @property
    def containers(self) -> _DummyContainers:
        return _DummyContainers()

    @property
    def images(self) -> _DummyImages:
        return _DummyImages()


class ContainerManager:
    """Manages container lifecycle with language-agnostic sandbox support."""

    def __init__(self) -> None:
        """Initialize container manager for MCP servers.

        This method attempts to create a real Docker client using environment
        configuration. On platforms where a unix socket is not available (e.g.
        Windows), it will try a named-pipe fallback. If no connection can be
        established, a minimal dummy client is used so the rest of the app can
        continue to run (container operations will be disabled and logged).
        """
        self.docker_host = Config.DOCKER_HOST
        # Get Python image from config
        self.python_image = Config.MCP_PYTHON_IMAGE
        # Get Node.js image from config
        self.node_image = Config.MCP_NODE_IMAGE

        self.docker_client: DockerClientProtocol = self._create_docker_client()

        # Respect configuration flag to keep containers between runs
        self.preserve_containers = Config.MCP_PRESERVE_CONTAINERS

        # Track containers that were reused to avoid cleanup
        self.reused_containers = set()
        # Secure file manager for handling secret files
        self.file_manager = SecureFileManager()

    def _create_docker_client(self) -> DockerClientProtocol:
        """Initialize the Docker client, falling back to a dummy implementation."""
        docker_client: DockerClientProtocol | None = None

        try:
            client = DockerClient.from_env(timeout=Config.DOCKER_TIMEOUT)
            logger.debug("Docker client created from environment.")
            docker_client = cast(DockerClientProtocol, client)
        except Exception as e:
            logger.warning(f"Failed to create Docker client from environment: {e}")

            try:
                import platform

                if platform.system().lower() == "windows":
                    try:
                        npipe_url = "npipe:////./pipe/docker_engine"
                        client = DockerClient(base_url=npipe_url, timeout=Config.DOCKER_TIMEOUT)
                        logger.info("Docker client connected using Windows named pipe.")
                        docker_client = cast(DockerClientProtocol, client)
                    except Exception as e2:
                        logger.warning(f"Windows named pipe Docker connection failed: {e2}")
            except Exception:
                pass

        if docker_client is not None:
            return docker_client

        logger.warning(
            "Docker client unavailable. Container-related features will be disabled."
        )
        return cast(DockerClientProtocol, _NoDockerClient())

    def _check_docker_running(self) -> bool:
        """Check if the Docker daemon is running."""
        try:
            self.docker_client.ping()
            return True
        except (APIError, ConnectionError, OSError):
            return False

    def get_image_tag(self, server: MCPServer) -> str:
        """Generate the Docker image tag for a server."""
        return f"mcp-anywhere/server-{server.id}"

    def _get_container_name(self, server_id: str) -> str:
        """Generate the container name for a server."""
        return f"mcp-{server_id}"

    def _is_container_healthy(self, server: MCPServer) -> bool:
        """Check if existing container is healthy and can be reused.

        Args:
            server: The MCP server configuration

        Returns:
            bool: True if container exists, is running, and has correct image
        """
        container_name = self._get_container_name(server.id)
        expected_image = self.get_image_tag(server)

        try:
            container = self.docker_client.containers.get(container_name)

            status = getattr(container, "status", "")
            if status != "running":
                if self.preserve_containers and status in {"created", "exited", "paused"}:
                    try:
                        logger.info(f"Restarting preserved container {container_name} (status: {status})")
                        container.start()
                        container.reload()
                        status = getattr(container, "status", "")
                    except APIError as exc:
                        logger.warning(f"Failed to restart preserved container {container_name}: {exc}")
                if status != "running":
                    logger.debug(
                        f"Container {container_name} is not running (status: {status})"
                    )
                    return False

            # Check if container uses the expected image
            container_image = (
                container.image.tags[0] if container.image.tags else container.image.id
            )
            if container_image != expected_image and not container_image.startswith(
                expected_image
            ):
                logger.debug(
                    f"Container {container_name} has wrong image: {container_image} != {expected_image}"
                )
                return False

            logger.info(f"Container {container_name} is healthy and can be reused")
            return True

        except (NotFound, IndexError, AttributeError):
            logger.debug(
                f"Container {container_name} not found or has invalid image info"
            )
            return False
        except APIError as e:
            logger.warning(f"Error checking container {container_name}: {e}")
            return False

    def _cleanup_existing_container(self, container_name: str) -> None:
        """Clean up existing container with the same name.

        Args:
            container_name: Name of the container to clean up
        """
        try:
            # Try to get the existing container
            existing_container = self.docker_client.containers.get(container_name)
            logger.info(f"Found existing container '{container_name}', cleaning up...")

            try:
                # Stop the container if it's running
                existing_container.stop(timeout=10)
                logger.info(f"Stopped existing container '{container_name}'")
            except APIError as e:
                logger.warning(f"Failed to stop container '{container_name}': {e}")

            try:
                # Remove the container
                existing_container.remove(force=True)
                logger.info(f"Removed existing container '{container_name}'")
            except APIError as e:
                logger.warning(f"Failed to remove container '{container_name}': {e}")

        except NotFound:
            # Container doesn't exist, nothing to clean up
            logger.debug(f"No existing container found with name '{container_name}'")
        except APIError as e:
            logger.exception(
                f"Docker API error while cleaning up container '{container_name}': {e}"
            )

    def get_container_error_logs(self, server_id: str, tail: int = 50) -> str:
        """Get recent logs from a container to help diagnose startup issues.

        Args:
            server_id: The server ID to get logs for
            tail: Number of recent log lines to retrieve

        Returns:
            String containing the container logs, or empty string if not available
        """
        container_name = self._get_container_name(server_id)
        try:
            # Try to get the container - include stopped containers
            container = self.docker_client.containers.get(container_name)

            # Get recent logs from both stdout and stderr
            logs = (
                container.logs(tail=tail, stderr=True, stdout=True, timestamps=False)
                .decode("utf-8", errors="ignore")
                .strip()
            )

            if logs:
                logger.debug(
                    f"Retrieved {len(logs.splitlines())} log lines from container {container_name}"
                )
                return logs
            else:
                logger.debug(f"No logs available from container {container_name}")
                return ""

        except NotFound:
            logger.debug(f"Container {container_name} not found")
            return ""
        except (APIError, ConnectionError, OSError) as e:
            logger.warning(f"Failed to get logs from container {container_name}: {e}")
            return ""

    def _extract_error_from_logs(self, logs: str) -> Optional[str]:
        """Extract a meaningful error message from container logs.

        Args:
            logs: Raw container logs

        Returns:
            Extracted error message or None if no clear error found
        """
        if not logs:
            return None

        # Prioritized list of regex patterns to find the root cause of an error.
        # The list is ordered from most specific (credentials, config) to most general.
        error_patterns = [
            # Credentials/auth errors (highest priority)
            r"([\w\s]+credentials not found[^\n]+)",
            r"(authentication failed[^\n]+)",
            r"(api key[^\n]+missing[^\n]+)",
            r"(api key[^\n]+not found[^\n]+)",
            r"(environment variable[^\n]+not (set|found)[^\n]+)",
            # Configuration errors
            r"(configuration[^\n]+not found[^\n]+)",
            r"(missing required[^\n]+)",
            # General but clear errors
            r"error:\s*([^\n]+)",
            r"exception:\s*([^\n]+)",
            r"failed to\s*([^\n]+)",
            # Log-formatted errors
            r"error\s+[-|:]\s*([^\n]+)",
            r"\[error\]\s*([^\n]+)",
        ]

        def clean_message(msg: str) -> str:
            """Cleans up a raw log line for display to the user."""
            msg = msg.strip(" -|:")
            # Remove timestamps, log levels, and module paths
            msg = re.sub(
                r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s*\|?\s*", "", msg
            )
            msg = re.sub(r"^(error|info|warning|debug)\s*[-|:]\s*", "", msg, flags=re.I)
            msg = re.sub(r"^[\w\.]+:\w+:\d+\s*[-|]\s*", "", msg)
            return msg.strip()

        # Search for the first match from our prioritized list
        for pattern in error_patterns:
            # Find all matches in the entire log blob
            matches = re.findall(pattern, logs, re.IGNORECASE | re.MULTILINE)
            if matches:
                # Use the last match found, as it's likely the most recent/relevant
                last_match = matches[-1]
                # Handle tuple results from regex groups
                message = last_match if isinstance(last_match, str) else last_match[0]
                return clean_message(message)

        return None

    def set_preserve_preference(self, preserve: bool) -> None:
        """Update container preservation preference in memory."""
        self.preserve_containers = preserve
        logger.debug(f"Container preservation preference set to {preserve}")

    async def load_preserve_setting(self) -> bool:
        """Refresh preservation preference from stored settings."""
        try:
            from mcp_anywhere.settings_store import get_effective_setting

            value = await get_effective_setting("containers.preserve")
            if value is None:
                self.preserve_containers = Config.MCP_PRESERVE_CONTAINERS
            else:
                self.preserve_containers = value.lower() in ("true", "1", "yes")
        except Exception as exc:
            logger.debug(f"Failed to load container preservation preference: {exc}")
            self.preserve_containers = Config.MCP_PRESERVE_CONTAINERS
        return self.preserve_containers

    def _image_exists(self, image_name: str) -> bool:
        """Check if a Docker image exists locally."""
        try:
            self.docker_client.images.get(image_name)
            return True
        except ImageNotFound:
            return False
        except (APIError, ConnectionError, OSError) as e:
            logger.debug(f"Failed to check image '{image_name}': {e}")
            return False

    def _ensure_image_exists(self, image_name: str) -> None:
        """Checks if a Docker image exists locally and pulls it if not."""
        try:
            self.docker_client.images.get(image_name)
            logger.info(f"Image '{image_name}' already exists locally.")
        except ImageNotFound:
            logger.info(f"Image '{image_name}' not found locally. Pulling...")
            try:
                self.docker_client.images.pull(image_name)
                logger.info(f"Successfully pulled image '{image_name}'.")
            except (APIError, ConnectionError, OSError) as e:
                logger.exception(f"Failed to pull image '{image_name}': {e}")

    def _get_env_vars(self, server: MCPServer) -> dict[str, str]:
        """Extract environment variables from server configuration."""
        env_vars = {}

        # Regular environment variables (existing functionality)
        for env_var in getattr(server, "env_variables", []):
            if env_var.get("value"):
                env_vars[env_var["key"]] = env_var["value"]

        # Access secret_files safely - may not be loaded for newly created servers
        for secret_file in getattr(server, "secret_files", []):
            if secret_file.is_active and secret_file.env_var_name:
                # Path inside the container where the file will be mounted
                container_path = f"/secrets/{secret_file.original_filename}"
                env_vars[secret_file.env_var_name] = container_path

        return env_vars

    def _parse_install_command(self, server: MCPServer) -> str:
        """Parse and validate install command for container execution."""
        cmd = server.install_command.strip()

        if not cmd:
            return ""

        # Basic validation - ensure it's not trying to do something harmful
        if any(danger in cmd.lower() for danger in ["rm -rf", "dd if=", "> /dev/"]):
            logger.warning(f"Potentially dangerous install command blocked: {cmd}")
            return ""

        cmd_parts = shlex.split(cmd)

        # For npx servers, we might need to transform the command
        if server.runtime_type == "npx":
            # Optimization flags to reduce npm install size and speed
            npm_flags = "--no-audit --omit=dev --no-optional"
            
            # If user provides "npx @package", transform to proper install
            if cmd_parts[0] == "npx":
                package = cmd_parts[1].strip()
                # Add optimization flags to speed up npm install and reduce size
                return f"npm install -g {npm_flags} {package}"
            # If already npm install, add optimization flags
            elif cmd_parts[0] == "npm" and cmd_parts[1] == "install":
                # Check if optimization flags are already present
                has_flags = any(flag in cmd for flag in ["--omit=dev", "--production", "--no-optional"])
                if not has_flags:
                    # Insert optimization flags after 'npm install' or 'npm install -g'
                    if "-g" in cmd_parts:
                        return cmd.replace("npm install -g", f"npm install -g {npm_flags}")
                    else:
                        return cmd.replace("npm install", f"npm install {npm_flags}")
                return cmd
            # Otherwise assume it's a package name
            else:
                return f"npm install -g {npm_flags} {cmd_parts[0]}"

        # For Python/uvx, ensure uv is installed first
        if server.runtime_type == "uvx":
            # For uvx servers, we need to install uv first
            # Since SandboxSession might not handle shell operators well,
            # we'll install uv separately in the build process
            return cmd if cmd.strip() else ""

        # For other Python packages, return as-is since pip handles their own parsing
        return cmd

    def _parse_start_command(self, server: MCPServer) -> list[str]:
        """Parse start command into Docker command array."""
        cmd = server.start_command.strip()

        if not cmd:
            return []

        try:
            # Use shlex to properly parse the command
            parts = shlex.split(cmd)

            if server.runtime_type in ["npx", "uvx"]:
                has_stdio_token = any("stdio" in part for part in parts)
                has_http_token = any("http" in part for part in parts)
                references_builtin_cli = any(
                    keyword in part
                    for part in parts
                    for keyword in ("mcp-anywhere", "mcp_anywhere", "fastmcp")
                )

                if (
                    not has_stdio_token
                    and not has_http_token
                    and references_builtin_cli
                    and "serve" in parts
                ):
                    parts.append("stdio")

            return parts

        except ValueError as e:
            # shlex parsing failed (e.g., unmatched quotes)
            logger.exception(f"Failed to parse command '{cmd}': {e}")
            # Fall back to simple split
            return cmd.split()

    def build_server_image(self, server: MCPServer) -> str:
        """Build a Docker image for an MCP server with dependencies pre-installed."""
        image_tag = self.get_image_tag(server)

        logger.info(
            f"Building Docker image for server {server.name} ({server.runtime_type})"
        )

        try:
            # Determine base image and install command
            if server.runtime_type == "npx":
                lang = "javascript"
                base_image = self.node_image
            elif server.runtime_type == "uvx":
                lang = "python"
                base_image = self.python_image
            else:
                raise ValueError(f"Unsupported runtime type: {server.runtime_type}")

            # Parse the install command for container execution
            install_command = self._parse_install_command(server)

            # Use LLM-Sandbox to create container and install dependencies
            with SandboxSession(
                lang=lang,
                image=base_image,
                client=self.docker_client,
                keep_template=True,
                commit_container=True,
                docker_host=self.docker_host,
                execution_timeout=Config.DOCKER_TIMEOUT,  # Use configured timeout
                default_timeout=Config.DOCKER_TIMEOUT,  # Use configured timeout
                runtime_config={
                    "mem_limit": "512m",  # Reduced memory to work within Fly.io constraints
                    "cpu_quota": 100000,  # 1 CPU to speed up builds
                },
            ) as session:
                # Progress logging
                logger.info(f"Step 1/3: Setting up container for {server.name}...")

                # For uvx servers, install uv first
                if server.runtime_type == "uvx":
                    logger.info("Installing uv for uvx server...")
                    uv_result = session.execute_command("pip install uv")
                    if uv_result.exit_code != 0:
                        raise RuntimeError(f"Failed to install uv: {uv_result.stderr}")
                    logger.info("uv installed successfully")

                # Create Python sandbox directory for mcp-python-interpreter if this is a uvx server
                if (
                    server.runtime_type == "uvx"
                    and "mcp-python-interpreter" in server.start_command
                ):
                    logger.info(
                        "Creating Python sandbox directory for mcp-python-interpreter..."
                    )
                    mkdir_result = session.execute_command(
                        "mkdir -p /data/python-sandbox && chmod 755 /data/python-sandbox"
                    )
                    if mkdir_result.exit_code != 0:
                        logger.warning(
                            f"Failed to create sandbox directory: {mkdir_result.stderr}"
                        )

                # Install dependencies only if install_command is not empty
                if install_command:
                    logger.info(f"Step 2/3: Installing {server.name} dependencies...")
                    logger.info(f"Running: {install_command}")
                    result = session.execute_command(install_command)

                    if result.exit_code != 0:
                        raise RuntimeError(
                            f"Failed to install {install_command}: {result.stderr}"
                        )

                    logger.info("Dependencies installed successfully")
                else:
                    logger.info(
                        "No install command provided, skipping dependency installation"
                    )

                # Get the container that was just used
                container = session.container
                if not container:
                    raise RuntimeError("Sandbox session did not create a container.")

                # Commit the container to a new image with explicit settings
                # Use a larger timeout and resource-friendly settings
                logger.info(f"Step 3/3: Creating image {image_tag}...")
                container.commit(
                    repository=image_tag,
                    conf={
                        "Cmd": None,  # Will be set at runtime
                        "WorkingDir": "/app",
                    },
                )

            logger.info(f"Successfully built and tagged image {image_tag}")
            return image_tag

        except (APIError, OSError, RuntimeError) as e:
            logger.error(f"Failed to build image for server {server.name}: {e}")
            raise

    def load_default_servers(
        self, json_file_path: str | None = None
    ) -> list[dict[str, Any]]:
        """Load default server configurations from JSON file."""
        if json_file_path is None:
            json_file_path = Config.DEFAULT_SERVERS_FILE

        try:
            if not os.path.exists(json_file_path):
                logger.warning(f"Default servers file not found: {json_file_path}")
                return []

            with open(json_file_path, encoding="utf-8") as f:
                servers_config = json.load(f)

            logger.info(
                f"Loaded {len(servers_config)} default server configurations from {json_file_path}"
            )
            return servers_config

        except json.JSONDecodeError as e:
            logger.exception(f"Failed to parse JSON file {json_file_path}: {e}")
            raise
        except (FileNotFoundError, PermissionError, OSError) as e:
            logger.exception(
                f"Failed to load default servers from {json_file_path}: {e}"
            )
            raise

    async def ensure_default_servers(self, json_file_path: str | None = None) -> None:
        """Ensure default servers exist in the database."""
        if json_file_path is None:
            json_file_path = Config.DEFAULT_SERVERS_FILE

        try:
            default_servers = self.load_default_servers(json_file_path)

            async with get_async_session() as session:
                for server_config in default_servers:
                    # Check if server already exists
                    stmt = select(MCPServer).where(
                        MCPServer.github_url == server_config.get("github_url")
                    )
                    result = await session.execute(stmt)
                    existing_server = result.scalar_one_or_none()

                    if not existing_server:
                        logger.info(
                            f"Creating default server: {server_config.get('name')}"
                        )
                        new_server = MCPServer(
                            name=server_config.get("name"),
                            github_url=server_config.get("github_url"),
                            description=server_config.get("description"),
                            runtime_type=server_config.get("runtime_type"),
                            install_command=server_config.get("install_command"),
                            start_command=server_config.get("start_command"),
                            is_active=server_config.get("is_active", True),
                            build_status=server_config.get("build_status", "pending"),
                        )

                        session.add(new_server)

                await session.commit()
                logger.info("Default servers ensured in database")

        except (RuntimeError, ValueError, OSError) as e:
            logger.exception(f"Failed to ensure default servers: {e}")
            raise

    async def initialize_and_build_servers(self) -> None:
        """Initialize MCP Anywhere resources: check Docker, ensure images, build servers."""
        logger.info("Initializing MCP Anywhere resources...")

        await self.load_preserve_setting()

        # 1. Check if Docker is running
        if not self._check_docker_running():
            logger.error(
                "Docker is not running. Please start Docker and restart the application."
            )
            raise RuntimeError("Docker daemon is not running")
        logger.info("Docker is running.")

        # 2. Ensure base images exist
        try:
            logger.info("Ensuring base Docker images exist...")
            self._ensure_image_exists(Config.MCP_NODE_IMAGE)
            self._ensure_image_exists(Config.MCP_PYTHON_IMAGE)
            logger.info("Base Docker images are available.")
        except (APIError, OSError, RuntimeError) as e:
            logger.exception(
                f"Failed to ensure base images: {e}. Please check your Docker setup."
            )
            raise

        # 3. Ensure default servers exist in the database
        try:
            await self.ensure_default_servers()
        except (RuntimeError, ValueError, OSError) as e:
            logger.exception(f"Failed to ensure default servers: {e}")
            raise

        # 4. Find all active servers and ensure they are ready (reuse or rebuild)
        logger.info("Ensuring all active server containers are ready...")

        async with get_async_session() as session:
            # Query servers within the session where we'll update them
            all_active_servers = await get_active_servers(session)

            if all_active_servers:
                logger.info(f"Found {len(all_active_servers)} active servers to check.")
                for server in all_active_servers:
                    try:
                        # Check if existing container is healthy and can be reused
                        if self._is_container_healthy(server):
                            logger.info(f"Reusing existing container for {server.name}")
                            # Track this container as reused to avoid cleanup during mounting
                            container_name = self._get_container_name(server.id)
                            self.reused_containers.add(container_name)
                            server.build_status = "built"
                            server.build_logs = "Reused existing healthy container"
                            await session.commit()
                            continue

                        existing_image_tag = server.image_tag or self.get_image_tag(server)
                        if existing_image_tag and self._image_exists(existing_image_tag):
                            logger.info(
                                f"Found existing image {existing_image_tag} for {server.name}; reusing without rebuild"
                            )
                            server.image_tag = existing_image_tag
                            server.build_status = "built"
                            server.build_logs = f"Reusing image {existing_image_tag}"
                            await session.commit()
                            continue

                        logger.info(f"Building image for {server.name}...")
                        server.build_status = "building"
                        server.build_logs = "Building..."
                        await session.commit()

                        image_tag = self.build_server_image(server)
                        server.build_status = "built"
                        server.image_tag = image_tag
                        server.build_logs = f"Successfully built image {image_tag}"
                        await session.commit()
                        logger.info(f"Successfully built image for {server.name}.")
                        logger.debug(
                            f"Server {server.name} build_status set to: {server.build_status}"
                        )
                    except (APIError, OSError, RuntimeError, ValueError) as e:
                        logger.error(f"Failed to build {server.name}: {e}")
                        server.build_status = "failed"
                        server.build_logs = str(e)
                        await session.commit()
            else:
                logger.info("No active servers found to build.")

    async def mount_built_servers(self, mcp_manager) -> None:
        """Mount all successfully built servers to the router and discover tools."""
        built_servers = await get_built_servers()
        logger.debug(f"Found {len(built_servers)} servers with build_status='built'")

        if built_servers:
            logger.info(f"Mounting {len(built_servers)} built servers...")

            async with get_async_session() as db_session:
                for server in built_servers:
                    container_name = self._get_container_name(server.id)
                    if container_name in self.reused_containers:
                        logger.debug(
                            f"Preserving running container {container_name} during mount"
                        )
                    else:
                        self._cleanup_existing_container(container_name)

                    try:
                        # Add server to MCP manager and discover tools
                        discovered_tools = await mcp_manager.add_server(server)

                        # Clear any previous errors on successful mount
                        server.build_error = None
                        await db_session.merge(server)
                        await db_session.commit()

                        # Store discovered tools in database (even if empty for new containers)
                        if discovered_tools:
                            await store_server_tools(
                                db_session, server, discovered_tools
                            )
                            logger.info(
                                f"Successfully mounted server '{server.name}' with {len(discovered_tools)} tools"
                            )
                        else:
                            logger.info(
                                f"Successfully mounted server '{server.name}' (tools will be discovered on first use)"
                            )
                    except (RuntimeError, ValueError, ConnectionError, OSError) as e:
                        # The server failed to start, now we get the logs and store the error
                        error_logs = self.get_container_error_logs(server.id)
                        error_msg = self._extract_error_from_logs(error_logs)

                        final_error = error_msg or str(e)
                        server.build_error = final_error
                        logger.error(
                            f"Server '{server.name}' failed to start: {final_error}"
                        )

                        # Save the error to the database
                        await db_session.merge(server)
                        await db_session.commit()

                        # Now that we've logged the error, clean up the failed container
                        self._cleanup_existing_container(
                            self._get_container_name(server.id)
                        )
        else:
            logger.info("No built servers to mount.")

    async def cleanup_all_containers(self) -> None:
        """Clean up all MCP server containers during shutdown."""
        await self.load_preserve_setting()
        cleanup_required = not (self.preserve_containers and not os.environ.get("PYTEST_CURRENT_TEST"))

        try:
            if not cleanup_required:
                logger.info("Preserving containers between runs; skipping cleanup step.")
            else:
                # Get all active servers to find their container names
                async with get_async_session() as session:
                    servers = await get_active_servers(session)

                if not servers:
                    logger.debug("No active servers found for cleanup.")
                    return

                logger.info(f"Cleaning up {len(servers)} server containers...")

                for server in servers:
                    container_name = self._get_container_name(server.id)
                    self._cleanup_existing_container(container_name)

                logger.info("Container cleanup complete.")

        except Exception as e:
            logger.error(f"Error during container cleanup: {e}")
        finally:
            # Close docker client connection
            try:
                self.docker_client.close()
                logger.debug("Docker client connection closed.")
            except Exception as e:
                logger.debug(f"Error closing docker client: {e}")

