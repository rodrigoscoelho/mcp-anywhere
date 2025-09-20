"""Tests for server activation toggle route."""

import uuid

import pytest
from sqlalchemy import select

from mcp_anywhere.auth.models import User
from mcp_anywhere.database import MCPServer, get_async_session


async def _set_admin_password(app, password: str) -> None:
    """Ensure the admin user has a known password for authentication."""

    async with app.state.get_async_session() as session:
        stmt = select(User).where(User.username == "admin")
        user = await session.scalar(stmt)
        assert user is not None
        user.set_password(password)
        session.add(user)
        await session.commit()


@pytest.mark.asyncio
async def test_toggle_server_route_updates_activation_state(app, client):
    """The toggle route should flip the server's is_active flag."""

    await _set_admin_password(app, "TogglePass123!")

    login_response = await client.post(
        "/auth/login",
        data={"username": "admin", "password": "TogglePass123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    assert login_response.headers.get("location") == "/"

    async with get_async_session() as session:
        server = MCPServer(
            name=f"Toggle Server {uuid.uuid4().hex[:6]}",
            github_url="https://github.com/example/toggle-server",
            description="Toggle test server",
            runtime_type="docker",
            install_command="",
            start_command="npm run start",
            is_active=True,
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        server_id = server.id

    response = await client.post(
        f"/servers/{server_id}/toggle",
        data={"redirect_to": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/"

    async with get_async_session() as session:
        refreshed = await session.get(MCPServer, server_id)
        assert refreshed is not None
        assert refreshed.is_active is False

    response = await client.post(
        f"/servers/{server_id}/toggle",
        data={"redirect_to": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    async with get_async_session() as session:
        refreshed = await session.get(MCPServer, server_id)
        assert refreshed is not None
        assert refreshed.is_active is True
