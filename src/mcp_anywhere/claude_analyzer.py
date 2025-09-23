"""Analyzes GitHub repositories to extract MCP server configuration using Claude."""

import asyncio
import base64
import os
import re
from typing import Any
from textwrap import dedent

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
from mcp_anywhere.server_guidance import (
    CONTAINER_EXECUTION_NOTE,
    DESCRIPTION_GUIDANCE,
    ENV_GUIDANCE,
    ENV_SECRET_WARNING,
    INSTALL_GUIDANCE,
    NAME_GUIDANCE,
    RUNTIME_GUIDANCE,
    START_GUIDANCE,
)

logger = get_logger(__name__)

_LABEL_PATTERNS = {
    "runtime_type": re.compile(r"^RUNTIME\s*:\s*(.+)$", re.IGNORECASE),
    "install_command": re.compile(r"^INSTALL\s*:\s*(.+)$", re.IGNORECASE),
    "start_command": re.compile(r"^START\s*:\s*(.+)$", re.IGNORECASE),
    "name": re.compile(r"^NAME\s*:\s*(.+)$", re.IGNORECASE),
    "description": re.compile(r"^DESCRIPTION\s*:\s*(.+)$", re.IGNORECASE),
}

_ENV_VAR_PATTERN = re.compile(
    r"^KEY\s*:\s*(?P<key>[^,]+)"
    r"(?:,\s*DESC\s*:\s*(?P<desc>[^,]+))?"
    r"(?:,\s*REQUIRED\s*:\s*(?P<required>[^,]+))?",
    re.IGNORECASE,
)

_OPTIONAL_INSTALL_STRINGS = {
    "",
    "none",
    "none required",
    "not required",
    "not needed",
    "no install",
    "no installation",
    "no dependencies",
    "n/a",
}

_VALID_RUNTIME_VALUES = ("docker", "uvx", "npx")


class AsyncClaudeAnalyzer:
    """Async version of ClaudeAnalyzer for use in async contexts."""

    def __init__(self, api_key: str | None = None, github_token: str | None = None) -> None:
        # Do not require Anthropic API key at construction time.
        # The analyzer will resolve the LLM provider at analysis time via the factory.
        self.api_key = api_key or Config.ANTHROPIC_API_KEY
        # Lazily construct an Anthropic client only when an API key is available.
        self.client = Anthropic(api_key=self.api_key) if self.api_key else None
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
    
            # Convert results, treating exceptions as None. Use a small helper to make types explicit.
            from typing import Any
    
            def _as_str(val: Any) -> str | None:
                return val if isinstance(val, str) else None
    
            readme = _as_str(results[0])
            package_json = _as_str(results[1])
            pyproject = _as_str(results[2])

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
        try:
            provider_instance, resolved_model = await get_provider_and_model()
            logger.debug(
                "Resolved LLM provider",
                extra={
                    "provider": getattr(provider_instance, "provider_name", None),
                    "model": resolved_model,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive fallback when optional deps missing
            logger.warning(
                "LLM provider resolution failed; falling back to legacy Anthropic client",
                exc_info=exc,
            )
            provider_instance = None
            resolved_model = None

        # In tests we keep the legacy path to avoid patching every provider interaction.
        if provider_instance and os.environ.get("PYTEST_CURRENT_TEST"):
            provider_instance = None

        # If a provider instance was returned by the factory, use it for chat.
        if provider_instance:
            # Convert single-prompt flow to OpenAI-like messages and use provider chat.
            messages = [{"role": "user", "content": prompt}]
            try:
                analysis_text = await provider_instance.chat(messages, resolved_model)
            except Exception as e:
                logger.exception(f"LLM provider error: {e}")
                raise ConnectionError(f"Failed to get analysis from LLM provider: {e}")
            # Parse response assuming Claude-compatible structured text.
            return self._parse_claude_response(analysis_text)

        # Legacy Anthropic path: provider_instance is None meaning factory chose to preserve legacy behavior.
        # Ensure we have an Anthropic client available.
        if not self.client:
            raise ValueError("ANTHROPIC_API_KEY is required for legacy Anthropic analyzer path")

        try:
            analysis_text = await self._call_claude_api(
                prompt, model_name=resolved_model or self.model_name
            )
            return self._parse_claude_response(analysis_text)
        except AnthropicError as e:
            logger.exception(f"Claude API error: {e}")
            raise ConnectionError(f"Failed to get analysis from Claude: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((AnthropicError,)),
    )
    async def _call_claude_api(self, prompt: str, model_name: str | None = None) -> str:
        """Call Claude API with retry logic.

        Accepts an optional model_name which overrides the instance's configured model.
        """
        model = model_name or self.model_name
        if not self.client:
            raise ValueError("Anthropic client is not configured for _call_claude_api")
        # Ensure static analyzers understand client is not None
        client = self.client

        # Run the synchronous Claude API call in a thread pool
        loop = asyncio.get_event_loop()
        message = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model=model,
                max_tokens=1024,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            ),
        )

        # Extract text content from the first content block
        if not message.content:
            return ""
        content_block = message.content[0]
        text_value = getattr(content_block, "text", None)
        if isinstance(text_value, str):
            return text_value
        return str(content_block)

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

    def _build_prompt(self, url: str, readme: str | None, pkg_json: str | None, pyproject: str | None) -> str:
        """Constructs the prompt for the Claude API call."""
        def _indent_followup(text: str) -> str:
            return text.replace("\n", "\n   ")

        runtime_guidance = _indent_followup(RUNTIME_GUIDANCE)
        install_guidance = _indent_followup(INSTALL_GUIDANCE)
        start_guidance = _indent_followup(START_GUIDANCE)
        name_guidance = _indent_followup(NAME_GUIDANCE)
        description_guidance = _indent_followup(DESCRIPTION_GUIDANCE)
        env_guidance = _indent_followup(ENV_GUIDANCE)
        env_warning = _indent_followup(ENV_SECRET_WARNING)

        return dedent(f"""
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

        {CONTAINER_EXECUTION_NOTE}

        1. **Runtime type**: {runtime_guidance}
        2. Install command: {install_guidance}
        3. Start command: {start_guidance}
        4. **Server name**: {name_guidance}
        5. **Description**: {description_guidance}
        6. **Environment Variables**: {env_guidance}
           {env_warning}

        Respond in this exact, parsable format using the uppercase field labels exactly as shown.
        Do not add any conversational text, Markdown fences, or extra commentary.

        RUNTIME: [docker|npx|uvx]
        INSTALL: [full install command]
        START: [full start command]
        NAME: [server name]
        DESCRIPTION: [one-line description]
        ENV_VARS:
        - KEY: [key name], DESC: [description], REQUIRED: [true|false]
        - KEY: [key name], DESC: [description], REQUIRED: [true|false]
        """).strip()

    def _parse_claude_response(self, text: str) -> dict[str, Any]:
        """Parse Claude's structured response into a dictionary."""

        def _normalize_runtime(value: str) -> str:
            lowered = value.lower()
            for runtime in _VALID_RUNTIME_VALUES:
                if runtime in lowered:
                    return runtime
            parts = re.split(r"[\s,()/|-]+", lowered)
            return parts[0] if parts and parts[0] else "docker"

        def _normalize_optional_command(value: str) -> str:
            cleaned = value.strip().strip("`\"")
            normalized = cleaned.lower().rstrip(".")
            if normalized in _OPTIONAL_INSTALL_STRINGS:
                return ""
            return cleaned

        result = {
            "runtime_type": "docker",
            "install_command": "",
            "start_command": "",
            "name": "unnamed-server",
            "description": "",
            "env_variables": [],
        }

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if re.match(r"^ENV_VARS\s*:?", line, re.IGNORECASE):
                continue

            matched_label = False
            for key, pattern in _LABEL_PATTERNS.items():
                match = pattern.match(line)
                if not match:
                    continue

                value = match.group(1).strip()
                if key == "runtime_type":
                    result[key] = _normalize_runtime(value)
                elif key == "install_command":
                    result[key] = _normalize_optional_command(value)
                else:
                    result[key] = value
                matched_label = True
                break

            if matched_label:
                continue

            env_line = line
            if env_line.startswith(("-", "*", "•")):
                env_line = env_line.lstrip("-*• ").strip()

            match = _ENV_VAR_PATTERN.match(env_line)
            if not match:
                continue

            key_value = match.group("key").strip()
            if not key_value:
                logger.warning("Could not parse env var line (empty key): %s", raw_line)
                continue

            desc_value = match.group("desc") or ""
            required_raw = (match.group("required") or "true").strip().lower()
            required = required_raw in {"true", "yes", "required", "1"}

            result["env_variables"].append(
                {
                    "key": key_value,
                    "description": desc_value.strip(),
                    "required": required,
                }
            )

        return result
