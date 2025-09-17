import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from mcp_anywhere.auth.models import User

# Helpers used by tests (kept local to this file)
async def _set_admin_password(app, password: str) -> None:
    """
    Set a known password for the 'admin' user in the test database.
    Uses the app.state.get_async_session() helper stored on the Starlette app.
    """
    async with app.state.get_async_session() as session:
        stmt = select(User).where(User.username == "admin")
        user = await session.scalar(stmt)
        assert user is not None, "Admin user should exist in test DB"
        user.set_password(password)
        session.add(user)
        await session.commit()


async def _login(client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    """
    Submit credentials to /auth/login and return the response.
    Caller is responsible for keeping the same client instance when needed so
    cookies (session cookie) are preserved between requests.
    """
    return await client.post(
        "/auth/login", data={"username": username, "password": password}, follow_redirects=False
    )


@pytest.mark.asyncio
async def test_change_password_success_flow(app):
    """
    Full success flow:
    1. Ensure admin has a known current password.
    2. Login as admin and preserve session cookie.
    3. POST to /auth/change-password with correct current password and matching new password.
    4. Expect redirect to /auth/login?changed=1
    5. Verify old password no longer works and new password does.
    """
    # Arrange - set known admin password
    await _set_admin_password(app, "OldPassword12345")

    # Use a client that preserves cookies for the login -> change-password flow
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Login
        resp = await _login(client, "admin", "OldPassword12345")
        assert resp.status_code == 302, "Login should redirect on success"

        # Act - change password
        change_data = {
            "current_password": "OldPassword12345",
            "new_password": "NewPassword123456!",
            "confirm_password": "NewPassword123456!",
        }
        resp = await client.post("/auth/change-password", data=change_data, follow_redirects=False)

        # Assert redirect to login with changed flag
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "/auth/login" in location
        assert "changed=1" in location

    # After password change the session was cleared server-side. Use fresh clients to validate auth.
    # Old password must fail
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client_old:
        resp_old = await _login(client_old, "admin", "OldPassword12345")
        assert resp_old.status_code == 302
        assert "/auth/login" in resp_old.headers.get("location", "")
        assert "error=invalid_credentials" in resp_old.headers.get("location", "")

    # New password must succeed
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client_new:
        resp_new = await _login(client_new, "admin", "NewPassword123456!")
        assert resp_new.status_code == 302
        # Successful login redirects to root "/"
        assert resp_new.headers.get("location", "") == "/"


@pytest.mark.asyncio
async def test_change_password_wrong_current_password(app):
    """
    If the provided current password is incorrect, the page should re-render and show the error.
    """
    await _set_admin_password(app, "OldPassword12345")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Login to obtain session cookie
        resp = await _login(client, "admin", "OldPassword12345")
        assert resp.status_code == 302

        # Attempt password change with wrong current password
        change_data = {
            "current_password": "WrongCurrentPassword",
            "new_password": "AnotherNewPassword123!",
            "confirm_password": "AnotherNewPassword123!",
        }
        resp = await client.post("/auth/change-password", data=change_data)
        assert resp.status_code == 200
        text = resp.text
        assert "Current password is incorrect." in text


@pytest.mark.asyncio
async def test_change_password_mismatched_confirmation(app):
    """
    If the new password and confirmation do not match, the page should show an appropriate error.
    """
    await _set_admin_password(app, "OldPassword12345")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await _login(client, "admin", "OldPassword12345")
        assert resp.status_code == 302

        change_data = {
            "current_password": "OldPassword12345",
            "new_password": "NewPasswordABC123!",
            "confirm_password": "DifferentPasswordXYZ!",
        }
        resp = await client.post("/auth/change-password", data=change_data)
        assert resp.status_code == 200
        assert "New password and confirmation do not match." in resp.text


@pytest.mark.asyncio
async def test_change_password_policy_min_length(app):
    """
    New password must meet minimum length policy (>=12). If not, the page shows the policy error.
    """
    await _set_admin_password(app, "OldPassword12345")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await _login(client, "admin", "OldPassword12345")
        assert resp.status_code == 302

        # New password too short
        change_data = {
            "current_password": "OldPassword12345",
            "new_password": "shortpwd",
            "confirm_password": "shortpwd",
        }
        resp = await client.post("/auth/change-password", data=change_data)
        assert resp.status_code == 200
        # The implementation uses the message "New password must be at least 12 characters long."
        assert "New password must be at least 12 characters long." in resp.text


@pytest.mark.asyncio
async def test_change_password_requires_authentication(app):
    """
    The change-password endpoints (GET and POST) should require an authenticated session and
    redirect unauthenticated users to the login page with a next=... query parameter.
    """
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # GET without authentication
        resp_get = await client.get("/auth/change-password", follow_redirects=False)
        assert resp_get.status_code == 302
        loc_get = resp_get.headers.get("location", "")
        assert "/auth/login" in loc_get
        assert "next=" in loc_get

        # POST without authentication
        resp_post = await client.post(
            "/auth/change-password",
            data={
                "current_password": "whatever",
                "new_password": "NewPassword12345!",
                "confirm_password": "NewPassword12345!",
            },
            follow_redirects=False,
        )
        assert resp_post.status_code == 302
        loc_post = resp_post.headers.get("location", "")
        assert "/auth/login" in loc_post
        assert "next=" in loc_post