from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from mcp_anywhere.auth.models import User
from mcp_anywhere.database import ToolUsageLog


async def _set_admin_password(app, password: str) -> None:
    async with app.state.get_async_session() as session:
        stmt = select(User).where(User.username == "admin")
        user = await session.scalar(stmt)
        assert user is not None
        user.set_password(password)
        session.add(user)
        await session.commit()


async def _login(client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


async def _create_tool_log(app) -> ToolUsageLog:
    async with app.state.get_async_session() as session:
        log = ToolUsageLog(
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            client_name="test-client",
            request_type="CallTool",
            server_id="srv",
            server_name="Test Server",
            tool_name="sample",
            full_tool_name="srv_sample",
            status="success",
            processing_ms=2187,
            arguments={"foo": "bar"},
            response={"content": []},
            error_message=None,
        )
        session.add(log)
        await session.commit()
        await session.refresh(log)
        return log


@pytest.mark.asyncio
async def test_tool_usage_dashboard_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/logs/tools", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers.get("location", "")


@pytest.mark.asyncio
async def test_tool_usage_detail_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/logs/tools/some-id", follow_redirects=False)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_tool_usage_dashboard_renders_with_logs(app) -> None:
    await _set_admin_password(app, "Password123!")
    log = await _create_tool_log(app)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as authed_client:
        login_response = await _login(authed_client, "admin", "Password123!")
        assert login_response.status_code == 302

        page = await authed_client.get("/logs/tools")
        assert page.status_code == 200
        assert "Tool Usage Analytics" in page.text
        assert "Test Server" in page.text
        assert "sample" in page.text

        detail = await authed_client.get(f"/logs/tools/{log.id}")
        assert detail.status_code == 200
        assert "Request Parameters" in detail.text
        assert "foo" in detail.text
