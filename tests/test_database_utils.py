import pytest
from sqlalchemy import select

from mcp_anywhere.database import MCPServer, MCPServerTool
from mcp_anywhere.database_utils import store_server_tools


@pytest.mark.asyncio
async def test_store_server_tools_persists_schema(db_session):
    server = MCPServer(
        name="Server With Schema",
        github_url="https://example.com/repo.git",
        runtime_type="docker",
        install_command="pip install -r requirements.txt",
        start_command="uv run start",
        env_variables=[],
    )
    db_session.add(server)
    await db_session.commit()

    schema = {"type": "object", "properties": {"foo": {"type": "string"}}}

    await store_server_tools(
        db_session,
        server,
        [
            {
                "name": "server.tool/foo",
                "description": "Example tool",
                "schema": schema,
            }
        ],
    )

    result = await db_session.execute(select(MCPServerTool))
    tool = result.scalar_one()
    assert tool.tool_schema == schema


@pytest.mark.asyncio
async def test_store_server_tools_updates_schema(db_session):
    server = MCPServer(
        name="Server Update Schema",
        github_url="https://example.com/repo.git",
        runtime_type="docker",
        install_command="pip install -r requirements.txt",
        start_command="uv run start",
        env_variables=[],
    )
    db_session.add(server)
    await db_session.commit()

    original_schema = {"type": "object", "properties": {}}
    updated_schema = {
        "type": "object",
        "properties": {"bar": {"type": "integer"}},
    }

    await store_server_tools(
        db_session,
        server,
        [
            {
                "name": "server.tool/bar",
                "description": "Tool",
                "schema": original_schema,
            }
        ],
    )

    await store_server_tools(
        db_session,
        server,
        [
            {
                "name": "server.tool/bar",
                "description": "Updated tool",
                "schema": updated_schema,
            }
        ],
    )

    stmt = select(MCPServerTool).where(MCPServerTool.tool_name == "server.tool/bar")
    result = await db_session.execute(stmt)
    tool = result.scalar_one()

    assert tool.tool_description == "Updated tool"
    assert tool.tool_schema == updated_schema
