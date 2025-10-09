import uuid

import pytest
from sqlalchemy import select
from fastmcp.exceptions import NotFoundError

from mcp_anywhere.auth.models import User
from mcp_anywhere.database import MCPServer, MCPServerTool, get_async_session
from mcp_anywhere.core.mcp_manager import MCPManager


class _TimeoutManager:
    def __init__(self):
        self.calls = 0

    def is_server_mounted(self, server_id: str) -> bool:
        return True

    async def get_runtime_tool(self, tool_key: str):
        self.calls += 1
        raise TimeoutError("timed out")


class _RuntimeToolStub:
    def __init__(self, key: str, description: str = "Echo tool"):
        self.key = key
        self.name = key.split("/", 1)[-1]
        self.description = description
        self.parameters = {"type": "object", "properties": {}}


class _MountedToolManagerStub:
    def __init__(self, runtime_tool: _RuntimeToolStub):
        self._runtime_tool = runtime_tool

    async def get_tools(self):
        return {self._runtime_tool.key: self._runtime_tool}


class _MountedServerStub:
    def __init__(self, runtime_tool: _RuntimeToolStub):
        self._tool_manager = _MountedToolManagerStub(runtime_tool)


class _RenamedToolManager:
    def __init__(self, server_id: str, runtime_tool: _RuntimeToolStub, missing_key: str):
        self._server_id = server_id
        self._runtime_tool = runtime_tool
        self._missing_key = missing_key
        self.mounted_servers = {server_id: _MountedServerStub(runtime_tool)}

    def is_server_mounted(self, server_id: str) -> bool:
        return server_id == self._server_id

    async def get_runtime_tool(self, tool_key: str):
        if tool_key == self._missing_key:
            raise NotFoundError(f"Tool {tool_key!r} not found")
        if tool_key == self._runtime_tool.key:
            return self._runtime_tool
        raise NotFoundError(f"Unexpected tool key {tool_key!r}")

    async def call_tool(self, tool_key: str, arguments: dict):
        if tool_key != self._runtime_tool.key:
            raise NotFoundError(f"Tool {tool_key!r} not found")
        return type(
            "CallResult",
            (object,),
            {
                "content": [{"type": "text", "text": "ok"}],
                "structured_content": None,
            },
        )()


async def _set_admin_password(app, password: str) -> None:
    async with app.state.get_async_session() as session:
        stmt = select(User).where(User.username == "admin")
        user = await session.scalar(stmt)
        assert user is not None
        user.set_password(password)
        session.add(user)
        await session.commit()


@pytest.mark.asyncio
async def test_server_detail_uses_cached_schema_on_timeout(app, client):
    async with get_async_session() as session:
        server_name = f"Timeout Server {uuid.uuid4().hex[:6]}"
        server = MCPServer(
            name=server_name,
            github_url="https://example.com/repo.git",
            runtime_type="docker",
            install_command="pip install -r requirements.txt",
            start_command="uv run start",
            env_variables=[],
            is_active=True,
            build_status="built",
        )
        session.add(server)
        await session.flush()

        tool = MCPServerTool(
            server_id=server.id,
            tool_name=f"{server_name}.tool/echo",
            tool_description="Echo tool",
            tool_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
            },
            is_enabled=True,
        )
        session.add(tool)
        await session.commit()

        server_id = server.id

    await _set_admin_password(app, "DetailPass123!")
    login_response = await client.post(
        "/auth/login",
        data={"username": "admin", "password": "DetailPass123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    timeout_manager = _TimeoutManager()
    app.state.mcp_manager = timeout_manager

    response = await client.get(f"/servers/{server_id}")
    assert response.status_code == 200
    assert "Tempo limite ao carregar as informações da ferramenta." in response.text
    assert timeout_manager.calls == 1


@pytest.mark.asyncio
async def test_server_detail_resyncs_tool_key(app, client):
    async with get_async_session() as session:
        suffix = uuid.uuid4().hex[:6]
        server = MCPServer(
            name=f"Renamed Server {suffix}",
            github_url="https://example.com/repo.git",
            runtime_type="docker",
            install_command="pip install -r requirements.txt",
            start_command="uv run start",
            env_variables=[],
            is_active=True,
            build_status="built",
        )
        session.add(server)
        await session.flush()

        stale_key = "oldprefix.tool/foo"
        tool = MCPServerTool(
            server_id=server.id,
            tool_name=stale_key,
            tool_description="Echo tool",
            tool_schema={"type": "object", "properties": {}},
            is_enabled=True,
        )
        session.add(tool)
        await session.commit()

        server_id = server.id
        tool_id = tool.id

    await _set_admin_password(app, "ResyncPass123!")
    login_response = await client.post(
        "/auth/login",
        data={"username": "admin", "password": "ResyncPass123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    prefix = MCPManager._format_prefix(server.name, server.id)
    new_key = f"{prefix}.tool/foo"
    runtime_tool = _RuntimeToolStub(new_key)
    app.state.mcp_manager = _RenamedToolManager(server_id, runtime_tool, stale_key)

    response = await client.get(f"/servers/{server_id}")
    assert response.status_code == 200
    assert "Refa�a o build do servidor" not in response.text
    assert new_key in response.text

    async with get_async_session() as session:
        refreshed_tool = await session.get(MCPServerTool, tool_id)
        assert refreshed_tool is not None
        assert refreshed_tool.tool_name == new_key


@pytest.mark.asyncio
async def test_tool_route_resyncs_tool_key(app, client):
    async with get_async_session() as session:
        suffix = uuid.uuid4().hex[:6]
        server = MCPServer(
            name=f"Resync Runner {suffix}",
            github_url="https://example.com/repo.git",
            runtime_type="docker",
            install_command="pip install -r requirements.txt",
            start_command="uv run start",
            env_variables=[],
            is_active=True,
            build_status="built",
        )
        session.add(server)
        await session.flush()

        stale_key = "outdated.tool/runner"
        tool = MCPServerTool(
            server_id=server.id,
            tool_name=stale_key,
            tool_description="Runner tool",
            tool_schema={"type": "object", "properties": {}},
            is_enabled=True,
        )
        session.add(tool)
        await session.commit()

        server_id = server.id
        tool_id = tool.id

    await _set_admin_password(app, "RunnerPass123!")
    login_response = await client.post(
        "/auth/login",
        data={"username": "admin", "password": "RunnerPass123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    prefix = MCPManager._format_prefix(server.name, server.id)
    new_key = f"{prefix}.tool/runner"
    runtime_tool = _RuntimeToolStub(new_key, description="Runner tool")
    app.state.mcp_manager = _RenamedToolManager(server_id, runtime_tool, stale_key)

    response = await client.post(
        f"/servers/{server_id}/tools/{tool_id}/test",
        data={},
    )
    assert response.status_code == 200
    assert "Ferramenta executada com sucesso" in response.text

    async with get_async_session() as session:
        refreshed_tool = await session.get(MCPServerTool, tool_id)
        assert refreshed_tool is not None
        assert refreshed_tool.tool_name == new_key

