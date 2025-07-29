"""
Manages container lifecycle for MCP servers in any language.

Supports:
- npx: Node.js/JavaScript servers
- uvx: Python servers
- docker: Any language via Docker images
"""

from typing import Dict, Any, Optional, List
from flask import Flask
from llm_sandbox import SandboxSession
from mcp_router.models import MCPServer
from mcp_router.config import Config
from docker import DockerClient
from docker.errors import ImageNotFound
import time
import shlex
from mcp_router.logging_config import get_logger

logger = get_logger(__name__)


class ContainerManager:
    """Manages container lifecycle with language-agnostic sandbox support"""

    def __init__(self, app: Optional[Flask] = None):
        """Initialize with optional Flask app for database access"""
        self.app = app

        self.docker_host = Config.DOCKER_HOST
        # Get Python image from config
        self.python_image = Config.MCP_PYTHON_IMAGE
        # Get Node.js image from config
        self.node_image = Config.MCP_NODE_IMAGE
        # Docker client with extended timeout for large operations
        self.docker_client: DockerClient = DockerClient.from_env(timeout=Config.DOCKER_TIMEOUT)

    def check_docker_running(self) -> bool:
        """Check if the Docker daemon is running."""
        try:
            self.docker_client.ping()
            return True
        except Exception:
            return False

    def get_image_tag(self, server: MCPServer) -> str:
        """Generate the Docker image tag for a server."""
        return f"mcp-router/server-{server.id}"

    def ensure_image_exists(self, image_name: str) -> None:
        """Checks if a Docker image exists locally and pulls it if not.

        Args:
            image_name: Docker image name to check/pull
        """
        try:
            self.docker_client.images.get(image_name)
            logger.info(f"Image '{image_name}' already exists locally.")
        except ImageNotFound:
            logger.info(f"Image '{image_name}' not found locally. Pulling...")
            try:
                self.docker_client.images.pull(image_name)
                logger.info(f"Successfully pulled image '{image_name}'.")
            except Exception as e:
                logger.error(f"Failed to pull image '{image_name}': {e}")

    def _get_env_vars(self, server: MCPServer) -> Dict[str, str]:
        """Extract environment variables from server configuration"""
        env_vars = {}
        for env_var in server.env_variables:
            if env_var.get("value"):
                env_vars[env_var["key"]] = env_var["value"]
        return env_vars

    def _parse_install_command(self, server: MCPServer) -> str:
        """Parse and validate install command for container execution.

        Note: Returns a string (not array) because it's executed via shell.

        Args:
            server: MCPServer instance with install_command

        Returns:
            Parsed install command suitable for shell execution
        """
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
            # If user provides "npx @package", transform to proper install
            if cmd_parts[0] == "npx":
                package = cmd_parts[1].strip()
                # Add --no-audit flag to speed up npm install
                return f"npm install -g --no-audit {package}"
            # If already npm install, add optimization flags
            elif cmd_parts[0] == "npm" and cmd_parts[1] == "install":
                # Add --no-audit if not already present
                if "--no-audit" not in cmd:
                    return cmd.replace("npm install", "npm install --no-audit")
                return cmd
            # Otherwise assume it's a package name
            else:
                return f"npm install -g --no-audit {cmd_parts[0]}"

        # For Python/uvx, return as-is since pip/uvx handle their own parsing
        return cmd

    def _parse_start_command(self, server: MCPServer) -> List[str]:
        """Parse start command into Docker command array.

        Uses shlex for proper shell parsing to handle quotes, spaces, etc.

        Args:
            server: MCPServer instance with start_command

        Returns:
            List of command parts suitable for Docker execution
        """
        cmd = server.start_command.strip()

        if not cmd:
            return []

        try:
            # Use shlex to properly parse the command
            parts = shlex.split(cmd)

            if server.runtime_type in ["npx", "uvx"]:
                # For stdio-based servers, ensure stdio transport is specified
                if "stdio" not in parts:
                    parts.append("stdio")
                return parts

            elif server.runtime_type == "python-module":
                # For python modules: "module.name" -> ["python3", "-m", "module.name"]
                # The command should just be the module name
                if parts[0] == "python" or parts[0] == "python3":
                    # Already properly formatted
                    return parts
                else:
                    # Just the module name, format it
                    return ["python3", "-m"] + parts

            else:
                # For other types, return parsed command as-is
                return parts

        except ValueError as e:
            # shlex parsing failed (e.g., unmatched quotes)
            logger.error(f"Failed to parse command '{cmd}': {e}")
            # Fall back to simple split
            return cmd.split()

    def test_server(self, server: MCPServer) -> Dict[str, Any]:
        """Test server by verifying its Docker image exists and can start.

        Simple approach: Try to run the actual command with a short timeout.
        If it starts without crashing immediately, it's likely valid.

        Args:
            server: MCPServer instance

        Returns:
            Dict containing test results
        """
        logger.info(f"Testing server: {server.name}")
        start_time = time.time()

        try:
            image_tag = self.get_image_tag(server)

            # First, verify image exists
            try:
                self.docker_client.images.get(image_tag)
            except ImageNotFound:
                return {
                    "status": "error",
                    "message": f"Docker image not found: {image_tag}",
                    "duration": time.time() - start_time,
                }

            # Get the actual command we'll run
            run_command = self._parse_start_command(server)
            if not run_command:
                return {
                    "status": "error",
                    "message": "No start command configured",
                    "duration": time.time() - start_time,
                }

            # Simple test: Run the container briefly and see if it starts
            # For MCP servers, they should stay running waiting for input
            container = None
            try:
                container = self.docker_client.containers.run(
                    image_tag,
                    command=run_command,
                    detach=True,
                    remove=False,  # We'll clean up manually
                    mem_limit="256m",
                    cpu_quota=50000,  # 0.5 CPU
                    environment=self._get_env_vars(server),
                    stdin_open=True,  # MCP servers need stdin
                    tty=False,
                )

                # Wait a moment to see if it crashes immediately
                time.sleep(1)

                # Check if still running
                container.reload()
                is_running = container.status == "running"

                # Get logs for diagnostics
                logs = container.logs(tail=20).decode("utf-8", errors="ignore")

                # Clean up
                try:
                    container.stop(timeout=2)
                    container.remove()
                except Exception:
                    pass

                if is_running:
                    return {
                        "status": "success",
                        "message": "Server started successfully",
                        "duration": time.time() - start_time,
                        "image_tag": image_tag,
                    }
                else:
                    return {
                        "status": "error",
                        "message": "Server exited immediately. Check logs.",
                        "logs_preview": logs[:200] if logs else "No logs",
                        "duration": time.time() - start_time,
                    }

            finally:
                # Ensure cleanup even if something goes wrong
                if container:
                    try:
                        container.remove(force=True)
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Error testing server {server.name}: {e}")
            return {
                "status": "error",
                "message": f"Test failed: {str(e)}",
                "duration": time.time() - start_time,
            }

    def build_server_image(self, server: MCPServer) -> str:
        """
        Build a Docker image for an MCP server with dependencies pre-installed.

        Uses LLM-Sandbox to create the container, install dependencies,
        then commits it as a reusable Docker image.

        Args:
            server: MCPServer instance to build image for

        Returns:
            str: Docker image tag for the built image

        Raises:
            BuildError: If image building fails
            RuntimeError: If dependency installation fails
        """
        image_tag = self.get_image_tag(server)

        logger.info(f"Building Docker image for server {server.name} ({server.runtime_type})")

        try:
            # Determine base image and install command
            if server.runtime_type == "npx":
                lang = "javascript"
                base_image = self.node_image
            elif server.runtime_type == "uvx":
                lang = "python"
                base_image = self.python_image
            elif server.runtime_type == "python-module":
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

                # Install dependencies only if install_command is not empty
                if install_command:
                    logger.info(f"Step 2/3: Installing {server.name} dependencies...")
                    logger.info(f"Running: {install_command}")
                    result = session.execute_command(install_command)

                    if result.exit_code != 0:
                        raise RuntimeError(
                            f"Failed to install {server.start_command}: {result.stderr}"
                        )

                    logger.info("Dependencies installed successfully")
                else:
                    logger.info("No install command provided, skipping dependency installation")

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

        except Exception as e:
            logger.error(f"Failed to build image for server {server.name}: {e}")
            raise
