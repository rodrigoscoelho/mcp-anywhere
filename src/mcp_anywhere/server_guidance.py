"""Shared guidance text for MCP server configuration fields."""

from __future__ import annotations

from textwrap import dedent

# Guidance text reused by the Claude analyzer prompt and the manual UI helper copy.
CONTAINER_EXECUTION_NOTE = (
    "IMPORTANT: The servers will run in containerized environments. Provide the full, "
    "actual commands that would be used to install and run the server."
)

RUNTIME_GUIDANCE = dedent(
    """
    Determine if this is 'npx' (for Node.js) or 'uvx' (for Python). Prioritize `pyproject.toml`
    for Python projects and `package.json` for Node.js projects.
    """
).strip()

INSTALL_GUIDANCE = dedent(
    """
    The full command to install the package or dependencies. This command runs during the
    container build process.
    - For npx packages: "npm install -g @org/package" (e.g., "npm install -g @modelcontextprotocol/server-filesystem")
    - For uvx packages: "uv tool install package-name" (e.g., "uv tool install mcp-server-git")
    """
).strip()

START_GUIDANCE = dedent(
    """
    The full command to run the server inside the container. This is exactly what you would
    type locally to start the MCP server (e.g., "npx @org/package" or "uvx package-name").
    """
).strip()

NAME_GUIDANCE = (
    "A short, descriptive, machine-readable name for the server (e.g., 'financial-data-api')."
)

DESCRIPTION_GUIDANCE = (
    "A brief, one-sentence description of the server's purpose."
)

ENV_GUIDANCE = dedent(
    """
    List the environment variables the server needs, why each one matters, and whether it is required.
    """
).strip()

ENV_SECRET_WARNING = dedent(
    """
    IMPORTANT: Do not include environment variables that point to secret file locations such as
    GOOGLE_APPLICATION_CREDENTIALS, AWS_SHARED_CREDENTIALS_FILE, KUBECONFIG, or any variable ending
    in _FILE, _PATH, _CERT, _KEY, or _CREDENTIALS. Those should be uploaded as secret files instead.
    """
).strip()

FIELD_GUIDANCE: dict[str, str] = {
    "name": NAME_GUIDANCE,
    "description": DESCRIPTION_GUIDANCE,
    "runtime_type": RUNTIME_GUIDANCE,
    "install_command": INSTALL_GUIDANCE,
    "start_command": START_GUIDANCE,
    "env_variables": f"{ENV_GUIDANCE}\n\n{ENV_SECRET_WARNING}",
}

__all__ = [
    "CONTAINER_EXECUTION_NOTE",
    "RUNTIME_GUIDANCE",
    "INSTALL_GUIDANCE",
    "START_GUIDANCE",
    "NAME_GUIDANCE",
    "DESCRIPTION_GUIDANCE",
    "ENV_GUIDANCE",
    "ENV_SECRET_WARNING",
    "FIELD_GUIDANCE",
]
