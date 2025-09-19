# AGENTS.md

This file provides guidance to agents when working with code in this repository.

- CLI & run (non-obvious):
  - Use `uv run mcp-anywhere serve stdio` for local Claude integration and `mcp-anywhere serve http` after install. See [`src/mcp_anywhere/__main__.py`](src/mcp_anywhere/__main__.py:90).
- Tests / test-mode (critical):
  - PYTEST_CURRENT_TEST toggles testing_mode; when set container initialization is skipped (use to avoid Docker in unit tests). See [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:72) and initialization gap at [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:82).
  - Test DB: tests create a temporary sqlite file in [`tests/conftest.py`](tests/conftest.py:42). Route-level tests use `httpx.ASGITransport` against `create_app(...)` — no real HTTP server required. See [`tests/conftest.py`](tests/conftest.py:89) and [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:40).
  - Run a single test from repo root: `python -m pytest path::test_name -q` (example: `python -m pytest tests/test_config_routes.py::test_config_instructions_stdio_mode -q`).
- Secrets & encryption (gotchas):
  - Secret files are encrypted on disk with an internal key stored at `.data/secrets/.encryption_key` managed by `SecureFileManager`; changing `Config.SECRET_KEY` does NOT re-encrypt existing secret files. See [`src/mcp_anywhere/security/file_manager.py`](src/mcp_anywhere/security/file_manager.py:45).
  - Always use `SecureFileManager` APIs to store/retrieve secret files; manual manipulation risks permission/encryption mismatches. See [`src/mcp_anywhere/security/file_manager.py`](src/mcp_anywhere/security/file_manager.py:16).
  - Values encrypted via `encrypt_value`/`decrypt_value` derive a Fernet key from `Config.SECRET_KEY`; rotating SECRET_KEY will break decrypts for DB-stored encrypted values. See [`src/mcp_anywhere/crypto_utils.py`](src/mcp_anywhere/crypto_utils.py:18).
  - For container mounts secret files are decrypted to temporary files named `temp_<stored_filename>` under the server dir — these are created at runtime and must be cleaned to avoid leaks. See [`src/mcp_anywhere/security/file_manager.py`](src/mcp_anywhere/security/file_manager.py:179).
- LLM provider & settings (non-obvious):
  - Resolution is DB > ENV via `get_effective_setting`. The LLM factory may intentionally return `(None, model)` to preserve legacy Anthropic flows — callers must handle a None provider. See [`src/mcp_anywhere/llm/factory.py`](src/mcp_anywhere/llm/factory.py:42) and Anthropic preservation at [`src/mcp_anywhere/llm/factory.py`](src/mcp_anywhere/llm/factory.py:70).
  - Use `get_effective_setting(...)` instead of reading `Config.*` for runtime behavior (it handles encrypted DB values, inference and provider-model rules). See [`src/mcp_anywhere/settings_store.py`](src/mcp_anywhere/settings_store.py:71).
  - `set_app_setting(..., encrypt=True)` stores ciphertext in DB; `get_effective_setting` will decrypt when returning values. See [`src/mcp_anywhere/settings_store.py`](src/mcp_anywhere/settings_store.py:21).
- Containers & Docker (operational gotchas):
  - Image and container naming is important: images are `mcp-anywhere/server-{server.id}` and containers `mcp-{server_id}` — changing these breaks reuse detection/cleanup. See [`src/mcp_anywhere/container/manager.py`](src/mcp_anywhere/container/manager.py:148).
  - Docker may be unavailable; ContainerManager falls back to a dummy client (`_NoDockerClient`) so the app keeps running but container ops are disabled. Expect ImageNotFound/NotFound behavior when Docker isn't available. See [`src/mcp_anywhere/container/manager.py`](src/mcp_anywhere/container/manager.py:132).
  - For diagnosing startup failures use ContainerManager.get_container_error_logs(server_id, tail) — `_extract_error_from_logs` runs prioritized regexes to present a short cause. See [`src/mcp_anywhere/container/manager.py`](src/mcp_anywhere/container/manager.py:244).
  - Container preservation preference is configurable (`MCP_PRESERVE_CONTAINERS`) and can be reloaded from settings_store. See [`src/mcp_anywhere/config.py`](src/mcp_anywhere/config.py:106) and [`src/mcp_anywhere/container/manager.py`](src/mcp_anywhere/container/manager.py:344).
  - Secret files mounted into containers are mapped to `/secrets/<original_filename>`; container env vars reference those paths. See [`src/mcp_anywhere/security/file_manager.py`](src/mcp_anywhere/security/file_manager.py:157) and [`src/mcp_anywhere/container/manager.py`](src/mcp_anywhere/container/manager.py:395).
- App lifecycle & architecture (non-obvious):
  - FastMCP produces an HTTP app with its own lifespan; the project deliberately uses `mcp_http_app.lifespan` when available — avoid duplicating lifespan logic. See [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:164).
  - Initialization order matters: DB init and OAuth seeding run before container build/mount. Reordering these steps causes startup failures. See [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:48) and container init at [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:82).
  - Long-lived managers (mcp_manager, container_manager, api_token_service) are stored on `app.state` — tests and other code expect them there or as None. See [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:181).
  - `mcp-anywhere reset` is destructive: it deletes `DATA_DIR` (DB, keys, container cache) and recreates an empty data dir. Use with caution. See [`src/mcp_anywhere/__main__.py`](src/mcp_anywhere/__main__.py:158).
  - Transport-specific features (OAuth, API tokens, session middleware) are enabled only when `transport_mode == "http"` — gate new features accordingly. See [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:120).
- Testing & local dev tips:
  - Route/UI tests use ASGI transport; no external HTTP server required. See [`tests/conftest.py`](tests/conftest.py:89).
  - When adding initialization calls in tests, remember `PYTEST_CURRENT_TEST` is used to avoid starting containers — use it to remove Docker dependency from unit tests. See [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:72).
- Linting & formatting:
  - Ruff is configured with `line-length = 100` and per-file ignores; don't change lint rules without updating [`pyproject.toml`](pyproject.toml:58).
- Misc / tooling:
  - Static files mount path is cwd-sensitive: `src/mcp_anywhere/web/static` — run commands/tests from repo root. See [`src/mcp_anywhere/web/app.py`](src/mcp_anywhere/web/app.py:145).
  - The UI supports "Analyze with Claude" (GitHub URL) to auto-populate server fields; analyzer results are assumed to follow server guidance. See docs and templates (instructions surfaced in UI) and [`docs/getting-started.md`](docs/getting-started.md:228).
  - Default servers can be loaded from `default_servers.json` (configurable via `DEFAULT_SERVERS_FILE` env). See [`env.example`](env.example:62).

Every line above is project-specific and was discovered by reading source files; generic framework defaults were intentionally omitted.