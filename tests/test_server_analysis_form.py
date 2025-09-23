import pytest
from sqlalchemy import select

from mcp_anywhere.auth.models import User
from mcp_anywhere.claude_analyzer import AsyncClaudeAnalyzer


@pytest.mark.asyncio
async def test_analyze_prefills_core_fields(app, client, monkeypatch):
    """Repository analysis should populate the key configuration fields."""

    sample_analysis = {
        "github_url": "https://github.com/example/demo",
        "name": "demo-server",
        "description": "Runs the demo MCP server",
        "runtime_type": "uvx",
        "install_command": "uv tool install demo-package",
        "start_command": "uvx demo-package start",
        "env_variables": [
            {"key": "API_KEY", "description": "", "required": True},
        ],
    }

    async def fake_analyze(self, url):
        return sample_analysis

    monkeypatch.setattr(
        AsyncClaudeAnalyzer, "analyze_repository", fake_analyze
    )

    async with app.state.get_async_session() as session:
        stmt = select(User).where(User.username == "admin")
        admin = await session.scalar(stmt)
        assert admin is not None
        admin.set_password("TestPassword123!")
        session.add(admin)
        await session.commit()

    login_response = await client.post(
        "/auth/login",
        data={"username": "admin", "password": "TestPassword123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    response = await client.post(
        "/servers/add",
        data={"github_url": sample_analysis["github_url"], "analyze": "1"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    body = response.text

    assert 'value="demo-server"' in body
    assert "Runs the demo MCP server" in body
    assert 'option value="uvx" selected' in body
    assert 'value="uv tool install demo-package"' in body
    assert 'value="uvx demo-package start"' in body
