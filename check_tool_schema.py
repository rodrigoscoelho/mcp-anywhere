#!/usr/bin/env python3
"""Check the schema of context7 tools in the database."""

import asyncio
import json
from sqlalchemy import select
from mcp_anywhere.database import get_async_session, init_db
from mcp_anywhere.database import MCPServerTool


async def main():
    """Check tool schemas."""
    await init_db()
    
    async with get_async_session() as session:
        # Find all tools
        stmt = select(MCPServerTool).limit(50)
        result = await session.execute(stmt)
        tools = result.scalars().all()

        print(f"Found {len(tools)} tools total:\n")

        # Show just tool names first
        for tool in tools:
            print(f"  - {tool.tool_name}")
        print("\n" + "="*80 + "\n")

        # Now find context7 tools
        context7_tools = [t for t in tools if 'context7' in t.tool_name.lower()]
        print(f"Found {len(context7_tools)} context7 tools:\n")

        for tool in context7_tools:
            print(f"Tool Name: {tool.tool_name}")
            print(f"Tool ID: {tool.id}")
            print(f"Server ID: {tool.server_id}")
            print(f"Is Enabled: {tool.is_enabled}")
            print(f"\nTool Schema:")
            if tool.tool_schema:
                print(json.dumps(tool.tool_schema, indent=2))
            else:
                print("  (No schema)")
            print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())

