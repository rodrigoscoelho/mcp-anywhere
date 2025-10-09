#!/usr/bin/env python3
"""Test calling context7 tool directly via MCP endpoint."""

import asyncio
import httpx
import json


async def main():
    """Test MCP endpoint."""
    base_url = "http://localhost:8000"
    
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        # Initialize MCP session
        print("1. Initializing MCP session...")
        init_response = await client.post(
            "/mcp/",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"
            },
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0.0"}
                }
            }
        )
        
        # Get session ID
        session_id = init_response.headers.get('mcp-session-id')
        print(f"   Session ID: {session_id}")
        
        # Send initialized notification
        print("\n2. Sending initialized notification...")
        await client.post(
            "/mcp/",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "mcp-session-id": session_id
            },
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }
        )
        
        # Call the tool with FULL NAME (with prefix)
        print("\n3. Calling context7_resolve-library-id with libraryName=aimsun...")
        tool_response = await client.post(
            "/mcp/",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "mcp-session-id": session_id
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "context7_resolve-library-id",
                    "arguments": {
                        "libraryName": "aimsun"
                    }
                }
            }
        )
        
        print(f"\n4. Response:")
        print(f"   Status: {tool_response.status_code}")
        print(f"\n   Body:")
        for line in tool_response.text.split('\n'):
            if line.startswith('data: '):
                try:
                    data = json.loads(line[6:])
                    print(f"   {json.dumps(data, indent=2)}")
                except:
                    print(f"   {line}")


if __name__ == "__main__":
    asyncio.run(main())

