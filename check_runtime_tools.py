#!/usr/bin/env python3
"""Check tool names in runtime."""

import asyncio
import httpx


async def main():
    """Check runtime tool names."""
    base_url = "http://localhost:8000"
    
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        # Login
        print("1. Logging in...")
        login_response = await client.post(
            "/auth/login",
            data={"username": "admin", "password": "Ropi1604mcp!"},
        )
        print(f"   Status: {login_response.status_code}")
        
        # Initialize MCP session
        print("\n2. Initializing MCP session...")
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
        
        # Extract session ID from response
        session_id = None
        for line in init_response.text.split('\n'):
            if line.startswith('data: '):
                import json
                try:
                    data = json.loads(line[6:])
                    if 'result' in data:
                        print(f"   Initialized: {data['result'].get('serverInfo', {}).get('name')}")
                except:
                    pass
        
        # Get session ID from headers
        session_id = init_response.headers.get('mcp-session-id')
        print(f"   Session ID: {session_id}")
        
        # Send initialized notification
        print("\n3. Sending initialized notification...")
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
        
        # List tools
        print("\n4. Listing tools...")
        tools_response = await client.post(
            "/mcp/",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "mcp-session-id": session_id
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list"
            }
        )
        
        # Parse tools from SSE response
        print("\n5. Tools from runtime:")
        for line in tools_response.text.split('\n'):
            if line.startswith('data: '):
                import json
                try:
                    data = json.loads(line[6:])
                    if 'result' in data and 'tools' in data['result']:
                        context7_tools = [
                            tool for tool in data['result']['tools']
                            if 'context7' in tool['name'].lower()
                        ]
                        for tool in context7_tools:
                            print(f"   - {tool['name']}")
                except:
                    pass


if __name__ == "__main__":
    asyncio.run(main())

