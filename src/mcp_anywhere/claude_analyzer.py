"""Analyzes GitHub repositories to extract MCP server configuration using Claude."""

import asyncio
import base64
import re
from typing import Any

import httpx
from anthropic import Anthropic, AnthropicError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# LLM provider factory (supports Anthropic and OpenRouter)
from mcp_anywhere.llm.factory import get_provider_and_model, PROVIDER_OPENROUTER

from mcp_anywhere.config import Config
from mcp_anywhere.logging_config import get_logger

logger = get_logger(__name__)


class AsyncClaudeAnalyzer:
    """Async version of ClaudeAnalyzer for use in async contexts."""

    def __init__(
        self, api_key: str | None = None, github_token: str | None = None
    ) -> None:
        self.api_key = api_key or Config.ANTHROPIC_API_KEY
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for AsyncClaudeAnalyzer")
        self.client = Anthropic(api_key=self.api_key)
        self.github_token = github_token or Config.GITHUB_TOKEN
        self.model_name = Config.ANTHROPIC_MODEL_NAME

    async def analyze_repository(self, github_url: str) -> dict[str, Any]:
        """Analyze a GitHub repository and return a structured configuration."""
        match = re.match(r"https://github\.com/([^/]+)/([^/]+)", github_url)
        if not match:
            raise ValueError(
                "Invalid GitHub URL format. Expected: https://github.com/owner/repo"
            )

        owner, repo = match.groups()

        try:
            # Fetch all files concurrently
            readme_task = self._fetch_file(owner, repo, "README.md")
            package_json_task = self._fetch_file(owner, repo, "package.json")
            pyproject_task = self._fetch_file(owner, repo, "pyproject.toml")

            results = await asyncio.gather(
                readme_task, package_json_task, pyproject_task, return_exceptions=True
            )

            # Check for critical errors (non-404 HTTP errors)
            for result in results:
                if (
                    isinstance(result, httpx.HTTPStatusError)
                    and result.response.status_code != 404
                ):
                    logger.error(f"GitHub API error: {result}")
                    raise ConnectionError(
                        f"Failed to fetch files from GitHub: {result.response.status_code}"
                    )

            # Convert results, treating exceptions as None
            readme = results[0] if not isinstance(results[0], Exception) else None
            package_json = results[1] if not isinstance(results[1], Exception) else None
            pyproject = results[2] if not isinstance(results[2], Exception) else None

        except ConnectionError:
            # Re-raise connection errors
            raise
        except (RuntimeError, TypeError, ValueError) as e:
            logger.exception(f"Unexpected error fetching files: {e}")
            raise ConnectionError(f"Failed to fetch files from GitHub: {e}")

        prompt = self._build_prompt(github_url, readme, package_json, pyproject)

        # Resolve LLM provider and model (DB > ENV). The factory may return None
        # as provider_instance to indicate the analyzer should keep the legacy
        # Anthropic path (preserves existing behavior and tests).
        provider_instance, resolved_model = await get_provider_and_model()
        logger.debug(
            "Resolved LLM provider",
            extra={"provider": getattr(provider_instance, "provider_name", None), "model": resolved_model},
        )

        # If OpenRouter provider selected, use it; otherwise preserve Anthropic flow.
        if provider_instance and getattr(provider_instance, "provider_name", None) == PROVIDER_OPENROUTER:
            # Convert single-prompt flow to OpenAI-like messages
            messages = [{"role": "user", "content": prompt}]
            try:
                analysis_text = await provider_instance.chat(messages, resolved_model)
            except Exception as e:
                logger.exception(f"OpenRouter provider error: {e}")
                raise ConnectionError(f"Failed to get analysis from OpenRouter: {e}")
            return self._parse_claude_response(analysis_text)

        # Default (Anthropic) path - keep original behavior for compatibility
        try:
            analysis_text = await self._call_claude_api(prompt)
            return self._parse_claude_response(analysis_text)
        except AnthropicError as e:
            logger.exception(f"Claude API error: {e}")
            raise ConnectionError(f"Failed to get analysis from Claude: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((AnthropicError,)),
    )
    async def _call_claude_api(self, prompt: str) -> str:
        """Call Claude API with retry logic."""
        # Run the synchronous Claude API call in a thread pool
        loop = asyncio.get_event_loop()
        message = await loop.run_in_executor(
            None,
            lambda: self.client.messages.create(
                model=self.model_name,
                max_tokens=1024,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            ),
        )

        # Extract text content from the first content block
        if hasattr(message.content[0], "text"):
            return message.content[0].text
        return str(message.content[0])

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def _fetch_file(self, owner: str, repo: str, path: str) -> str | None:
        """Fetch file content from a public GitHub repository using async httpx."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                content = response.json()["content"]
                return base64.b64decode(content).decode("utf-8")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None  # File not found is not an error
            raise
        except (httpx.RequestError, ValueError, KeyError, UnicodeDecodeError) as e:
            logger.warning(f"Could not fetch {path} from {owner}/{repo}: {e}")
            return None

    def _build_prompt(
        self, url: str, readme: str | None, pkg_json: str | None, pyproject: str | None
    ) -> str:
        """Constructs the prompt for the Claude API call."""
        return f"""
Analyze the following MCP server repository to determine its configuration.

Repository URL: {url}

Here are the contents of key files:

<file path="README.md">
{readme or "Not found."}
</file>

<file path="package.json">
{pkg_json or "Not found."}
</file>

<file path="pyproject.toml">
{pyproject or "Not found."}
</file>

Based on the file contents, extract the following information. Be precise and concise.

IMPORTANT: The servers will run in containerized environments. Provide the full, actual commands that would be used to install and run the server.

1. **Runtime type**: Determine if this is 'npx' (for Node.js) or 'uvx' (for Python). Prioritize `pyproject.toml` for Python projects and `package.json` for Node.js projects.

  2. Install command: The full command to install the package/dependencies. This command will be run during the container build
  process.
  - For npx packages: "npm install -g @org/package" (e.g., "npm install -g @modelcontextprotocol/server-filesystem")
  - For uvx packages: "uv tool install package-name" (e.g., "uv tool install mcp-server-git")

  3. Start command: The full command to run the server. This is the exact command you would type to start the server.
  - For npx: "npx @org/package" (e.g., "npx @modelcontextprotocol/server-filesystem")
  - For uvx: "uvx package-name" (e.g., "uvx mcp-server-git")

4. **Server name**: A short, descriptive, machine-readable name for the server (e.g., "financial-data-api").

5. **Description**: A brief, one-sentence description of the server's purpose.

6. **Environment Variables**: List any required environment variables, their purpose, and if they are required.
   IMPORTANT: DO NOT include environment variables that point to secret file locations such as:
   - GOOGLE_APPLICATION_CREDENTIALS
   - AWS_SHARED_CREDENTIALS_FILE
   - KUBECONFIG
   - Any variable ending in _FILE, _PATH, _CERT, _KEY, or _CREDENTIALS that refers to a file path
   These file-based secrets should be configured through the secrets interface, not as environment variables.

Respond in this exact, parsable format. Do not add any conversational text or pleasantries.

RUNTIME: [npx|uvx]
INSTALL: [full install command]
START: [full start command]
NAME: [server name]
DESCRIPTION: [one-line description]
ENV_VARS:
- KEY: [key name], DESC: [description], REQUIRED: [true|false]
- KEY: [key name], DESC: [description], REQUIRED: [true|false]
"""

    def _parse_claude_response(self, text: str) -> dict[str, Any]:
        """Parse Claude's structured response into a dictionary."""
        result = {
            "runtime_type": "docker",
            "install_command": "",
            "start_command": "",
            "name": "unnamed-server",
            "description": "",
            "env_variables": [],
        }

        for line in text.splitlines():
            if line.startswith("RUNTIME:"):
                result["runtime_type"] = line.split(":", 1)[1].strip()
            elif line.startswith("INSTALL:"):
                cmd = line.split(":", 1)[1].strip()
                # Keep the full command, including "none" if specified
                result["install_command"] = cmd if cmd.lower() != "none" else ""
            elif line.startswith("START:"):
                result["start_command"] = line.split(":", 1)[1].strip()
            elif line.startswith("NAME:"):
                result["name"] = line.split(":", 1)[1].strip()
            elif line.startswith("DESCRIPTION:"):
                result["description"] = line.split(":", 1)[1].strip()
            elif line.startswith("- KEY:"):
                try:
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 1:
                        continue

                    # Extract key (required)
                    key_part = parts[0].split(":", 1)
                    if len(key_part) < 2:
                        logger.warning(
                            f"Could not parse env var line (missing key): {line}"
                        )
                        continue
                    key = key_part[1].strip()

                    # Extract description (optional, but line must have proper format)
                    desc = ""
                    if len(parts) > 1 and "DESC:" in parts[1]:
                        desc = parts[1].split(":", 1)[1].strip()
                    elif len(parts) > 1:
                        # Line has comma but no DESC: - this is malformed
                        logger.warning(
                            f"Could not parse env var line (malformed DESC): {line}"
                        )
                        continue

                    # Extract required flag (optional, defaults to true)
                    required = True
                    if len(parts) > 2 and "REQUIRED:" in parts[2]:
                        req_str = parts[2].split(":", 1)[1].strip()
                        required = req_str.lower() == "true"

                    result["env_variables"].append(
                        {"key": key, "description": desc, "required": required}
                    )
                except (IndexError, ValueError) as e:
                    logger.warning(f"Could not parse env var line: {line} - {e}")

        return result
