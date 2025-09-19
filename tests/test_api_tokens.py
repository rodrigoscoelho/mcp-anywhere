import re

import httpx
import pytest
from sqlalchemy import select

from mcp_anywhere.auth.models import APIToken, User


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
async def test_api_token_routes_require_auth(client: httpx.AsyncClient):
    resp_get = await client.get("/settings/api-keys", follow_redirects=False)
    assert resp_get.status_code == 302
    assert "/auth/login" in resp_get.headers.get("location", "")

    resp_post = await client.post(
        "/settings/api-keys",
        data={"action": "create", "name": "Test Token"},
        follow_redirects=False,
    )
    assert resp_post.status_code == 302
    assert "/auth/login" in resp_post.headers.get("location", "")


@pytest.mark.asyncio
async def test_api_token_generation_flow(app):
    await _set_admin_password(app, "AdminPassword123!")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as authed_client:
        login_resp = await _login(authed_client, "admin", "AdminPassword123!")
        assert login_resp.status_code == 302

        resp = await authed_client.post(
            "/settings/api-keys",
            data={"action": "create", "name": "Automation"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "message=created" in resp.headers.get("location", "")

        page = await authed_client.get("/settings/api-keys")
        assert page.status_code == 200
        assert "New token: Automation" in page.text

        match = re.search(r"<code[^>]*>([A-Za-z0-9_\-]+)</code>", page.text)
        assert match, "Generated token should be displayed in code block"
        raw_token = match.group(1)

    async with app.state.get_async_session() as session:
        tokens = await session.execute(select(APIToken))
        stored = tokens.scalars().all()
        assert len(stored) == 1
        stored_token = stored[0]
        assert stored_token.name == "Automation"
        assert stored_token.token_prefix == raw_token[:8]
        assert stored_token.token_hint == raw_token[-4:]
        assert stored_token.token_hash != raw_token


@pytest.mark.asyncio
async def test_api_token_authorizes_mcp_requests(app):
    await _set_admin_password(app, "AnotherPass123!")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as authed_client:
        login_resp = await _login(authed_client, "admin", "AnotherPass123!")
        assert login_resp.status_code == 302

        resp = await authed_client.post(
            "/settings/api-keys",
            data={"action": "create", "name": "CI"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        page = await authed_client.get("/settings/api-keys")
        match = re.search(r"<code[^>]*>([A-Za-z0-9_\-]+)</code>", page.text)
        assert match
        raw_token = match.group(1)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api_client:
        no_auth = await api_client.get("/mcp/", follow_redirects=False)
        assert no_auth.status_code == 401

        headers = {"Authorization": f"Bearer {raw_token}"}
        authed_resp = await api_client.get("/mcp/", headers=headers, follow_redirects=False)
        # The downstream FastMCP app may respond with redirect/404/405, but should not be 401 now
        assert authed_resp.status_code != 401

    async with app.state.get_async_session() as session:
        stmt = select(APIToken)
        token = (await session.execute(stmt)).scalar_one()
        assert token.last_used_at is not None
