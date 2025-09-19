import httpx
import pytest
from sqlalchemy import select

from mcp_anywhere.auth.models import User
from mcp_anywhere.settings_store import get_effective_setting


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


@pytest.mark.asyncio
async def test_security_settings_require_auth(client: httpx.AsyncClient):
    resp = await client.get("/settings/security", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_security_settings_toggle(app):
    await _set_admin_password(app, "AdminPassword123!")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as authed_client:
        login_resp = await _login(authed_client, "admin", "AdminPassword123!")
        assert login_resp.status_code == 302

        disable_resp = await authed_client.post(
            "/settings/security",
            data={"mode": "disable"},
            follow_redirects=False,
        )
        assert disable_resp.status_code == 302
        assert "/settings/security" in disable_resp.headers.get("location", "")

        assert app.state.mcp_auth_disabled is True
        effective = await get_effective_setting("mcp.disable_auth")
        assert effective == "true"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon_client:
        resp = await anon_client.get("/mcp/", follow_redirects=False)
        assert resp.status_code != 401

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as authed_client:
        login_resp = await _login(authed_client, "admin", "AdminPassword123!")
        assert login_resp.status_code == 302

        enable_resp = await authed_client.post(
            "/settings/security",
            data={"mode": "require"},
            follow_redirects=False,
        )
        assert enable_resp.status_code == 302

        assert app.state.mcp_auth_disabled is False
        effective = await get_effective_setting("mcp.disable_auth")
        assert effective == "false"

