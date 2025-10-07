import uuid

import pytest
from sqlalchemy import select

from mcp_anywhere.auth.models import User
from mcp_anywhere.database import MCPServer, MCPServerTool, get_async_session


class _TimeoutManager:
    def __init__(self):
        self.calls = 0

    def is_server_mounted(self, server_id: str) -> bool:
        return True

    async def get_runtime_tool(self, tool_key: str):
        self.calls += 1
        raise TimeoutError("timed out")


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

