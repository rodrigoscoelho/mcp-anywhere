# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Overview

- Project: MCP Anywhere (mcp-anywhere) — a unified gateway for Model Context Protocol (MCP) servers.
- Language: Python (requires >=3.11)
- Entry point: mcp-anywhere console script -> mcp_anywhere.__main__:run_cli

Common Commands

- Install (development):
  - uv sync
  - or: pip install -e .

- Run server (HTTP mode, production-style):
  - mcp-anywhere serve http --host 0.0.0.0 --port 8000
  - python -m mcp_anywhere serve http

- Run server (STDIO mode, for local Claude Desktop integration):
  - mcp-anywhere serve stdio
  - python -m mcp_anywhere serve stdio

- Connect as client (STDIO gateway):
  - mcp-anywhere connect

- Reset data (destructive):
  - mcp-anywhere reset --confirm

- Admin reset password:
  - mcp-anywhere admin reset-password --username admin

Testing & Linting

- Run tests:
  - uv run pytest
  - pytest

- Run tests with coverage:
  - uv run pytest --cov=mcp_anywhere

- Lint (ruff):
  - uv run ruff check src/ tests/

- Type checking (mypy):
  - uv run mypy src/

Development & Debug

- Debug mode:
  - LOG_LEVEL=DEBUG mcp-anywhere serve http

- Docker compose (native):
  - docker compose -f docker-compose.native.yml up -d --build

High-level Architecture

- mcp_anywhere package (src/mcp_anywhere): core implementation. Major subpackages:
  - auth: OAuth, API token management, user models, token verification (mcp_anywhere/auth/*)
  - web: Web UI routes, templates, static assets (mcp_anywhere/web/*)
  - transport: MCP transport layers (HTTP server, STDIO server, client gateway)
  - llm: Providers and factory for LLM backends (anthropic, openrouter, etc.)
  - container: Docker container manager for isolating MCP tools
  - security: Secret file management and encryption utilities
  - core: Manager and middleware for server lifecycle

- Entry point and CLI: src/mcp_anywhere/__main__.py provides the CLI argument parsing and subcommands (serve, connect, reset, admin).
  - run_cli() calls asyncio.run(main()) (mcp_anywhere/__main__.py:294)
  - Server modes delegate to transport modules: run_http_server, run_stdio_server, run_connect_gateway (files: transport/http_server.py, transport/stdio_server.py, transport/stdio_gateway.py)

- Configuration: central config is in src/mcp_anywhere/config.py with defaults used by CLI and server components.

- Database: async SQLAlchemy + aiosqlite backend (see src/mcp_anywhere/database.py). Initialization and migrations happen during startup.

- Templates & static assets: Web UI templates under src/mcp_anywhere/web/templates and static files under src/mcp_anywhere/web/static.

Notes for future Claude Code instances

- Use the project script `mcp-anywhere` (registered entry point in pyproject.toml) to run CLI commands—this ensures correct environment and packaging.
- Avoid changing secrets or credential handling unless explicitly requested; treat secret file handling code (mcp_anywhere/security/file_manager.py) as security-sensitive.
- For running tests locally prefer using the uv tool (uv sync / uv run pytest) to respect the project's build/setup. If uv is unavailable, falling back to pip install -e . and pytest is acceptable.
- Key files to reference when making larger changes:
  - src/mcp_anywhere/__main__.py: CLI and entrypoint (file location: src/mcp_anywhere/__main__.py:1)
  - src/mcp_anywhere/transport/http_server.py: HTTP server startup (src/mcp_anywhere/transport/http_server.py:1)
  - src/mcp_anywhere/container/manager.py: Container lifecycle and cleanup (src/mcp_anywhere/container/manager.py:1)
  - src/mcp_anywhere/security/file_manager.py: Secret file encryption and mounting (src/mcp_anywhere/security/file_manager.py:1)

Repository-specific rules

- The project uses Ruff and mypy configured in pyproject.toml. Follow those configurations when adding or linting code.
- When modifying CLI behavior, update both the argparse configuration in __main__.py and the corresponding entry points in transport modules.

That should be sufficient for future Claude Code agents to get started in this repository.