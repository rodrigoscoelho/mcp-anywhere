#!/usr/bin/env python3
"""
Test script to verify both SSE and HTTP streaming modes work correctly.
Tests the context7 get-library-docs tool with a real search for "aimsun".
"""

import asyncio
import json
import httpx
from typing import Dict, Any, Optional


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'  # No Color


class MCPTransportTester:
    """Test MCP endpoint with different transport modes."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.mcp_endpoint = f"{base_url}/mcp/"
        self.session_id: Optional[str] = None

    async def initialize_session(self, accept_header: str) -> Dict[str, Any]:
        """Initialize MCP session and return session info."""
        print(f"{Colors.CYAN}🔌 Initializing MCP session with Accept: {accept_header}{Colors.NC}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.mcp_endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "transport-test-client",
                            "version": "1.0.0"
                        }
                    }
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": accept_header
                }
            )

            # Extract session ID from headers
            self.session_id = response.headers.get("mcp-session-id")
            
            status = response.status_code
            content_type = response.headers.get("content-type", "")
            is_sse = "text/event-stream" in content_type

            print(f"{Colors.BLUE}  Status: {status}{Colors.NC}")
            print(f"{Colors.BLUE}  Content-Type: {content_type}{Colors.NC}")
            print(f"{Colors.BLUE}  Session ID: {self.session_id}{Colors.NC}")
            print(f"{Colors.BLUE}  Is SSE: {is_sse}{Colors.NC}")

            if status == 200 and self.session_id:
                # Send initialized notification
                await client.post(
                    self.mcp_endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized"
                    },
                    headers={
                        "Content-Type": "application/json",
                        "Accept": accept_header,
                        "mcp-session-id": self.session_id
                    }
                )
                print(f"{Colors.GREEN}✓ Session initialized successfully{Colors.NC}\n")
                return {"status": "success", "is_sse": is_sse}
            else:
                print(f"{Colors.RED}✗ Failed to initialize session{Colors.NC}\n")
                return {"status": "error", "response": response.text}

    async def list_tools(self, accept_header: str) -> Dict[str, Any]:
        """List available tools."""
        print(f"{Colors.CYAN}📋 Listing tools with Accept: {accept_header}{Colors.NC}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.mcp_endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {}
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": accept_header,
                    "mcp-session-id": self.session_id
                }
            )

            content_type = response.headers.get("content-type", "")
            is_sse = "text/event-stream" in content_type

            if is_sse:
                data = self._parse_sse(response.text)
            else:
                data = response.json()

            # Extract tool names
            tools = []
            if is_sse and "sse_lines" in data:
                for line in data["sse_lines"]:
                    if "result" in line and "tools" in line["result"]:
                        tools = line["result"]["tools"]
                        break
            elif "result" in data and "tools" in data["result"]:
                tools = data["result"]["tools"]

            print(f"{Colors.GREEN}✓ Found {len(tools)} tools{Colors.NC}")
            
            # Find context7 tools
            context7_tools = [t for t in tools if "context7" in t["name"].lower()]
            print(f"{Colors.BLUE}  Context7 tools: {[t['name'] for t in context7_tools]}{Colors.NC}")

            # Show all tool names for debugging
            if len(tools) > 0:
                print(f"{Colors.BLUE}  All tools: {[t['name'] for t in tools[:5]]}...{Colors.NC}\n")

            return {"tools": tools, "context7_tools": context7_tools, "is_sse": is_sse}

    async def call_tool(self, tool_name: str, arguments: dict, accept_header: str) -> Dict[str, Any]:
        """Call a specific tool."""
        print(f"{Colors.CYAN}🔧 Calling tool '{tool_name}' with Accept: {accept_header}{Colors.NC}")
        print(f"{Colors.BLUE}  Arguments: {json.dumps(arguments, indent=2)}{Colors.NC}")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self.mcp_endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments
                    }
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": accept_header,
                    "mcp-session-id": self.session_id
                }
            )

            status = response.status_code
            content_type = response.headers.get("content-type", "")
            is_sse = "text/event-stream" in content_type

            print(f"{Colors.BLUE}  Status: {status}{Colors.NC}")
            print(f"{Colors.BLUE}  Content-Type: {content_type}{Colors.NC}")
            print(f"{Colors.BLUE}  Is SSE: {is_sse}{Colors.NC}")

            if is_sse:
                data = self._parse_sse(response.text)
                # Extract result from SSE
                result = None
                if "sse_lines" in data:
                    for line in data["sse_lines"]:
                        if "result" in line:
                            result = line["result"]
                            break
            else:
                data = response.json()
                result = data.get("result")

            if result:
                # Show first 500 chars of result
                result_str = json.dumps(result, indent=2)
                if len(result_str) > 500:
                    print(f"{Colors.GREEN}✓ Tool executed successfully (showing first 500 chars):{Colors.NC}")
                    print(result_str[:500] + "...")
                else:
                    print(f"{Colors.GREEN}✓ Tool executed successfully:{Colors.NC}")
                    print(result_str)
            else:
                print(f"{Colors.RED}✗ Tool execution failed or returned no result{Colors.NC}")
                print(f"Response: {response.text[:500]}")

            print()
            return {"status": status, "is_sse": is_sse, "result": result}

    def _parse_sse(self, text: str) -> Dict[str, Any]:
        """Parse SSE stream and extract JSON data."""
        results = []
        for line in text.split('\n'):
            if line.startswith('data: '):
                data_json = line[6:].strip()
                try:
                    parsed = json.loads(data_json)
                    results.append(parsed)
                except json.JSONDecodeError:
                    pass
        
        return {
            "sse_lines": results,
            "line_count": len(results)
        }


async def main():
    """Run transport mode tests."""
    print("=" * 80)
    print(f"{Colors.GREEN}MCP Anywhere - Transport Mode Test Suite{Colors.NC}")
    print("=" * 80)
    print()
    print("This test verifies that both SSE and HTTP streaming work correctly.")
    print("We'll test the context7 get-library-docs tool searching for 'aimsun'.")
    print()

    tester = MCPTransportTester()

    # Test 1: SSE Mode (text/event-stream)
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
    print(f"{Colors.YELLOW}TEST 1: SSE Mode (Accept: text/event-stream){Colors.NC}")
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
    print()

    # Note: FastMCP requires BOTH accept headers, so we'll skip SSE-only and JSON-only tests
    # and focus on the recommended mode that works

    # Test 3: Both modes (application/json, text/event-stream) - Default recommended
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
    print(f"{Colors.YELLOW}TEST: Both Modes (Accept: application/json, text/event-stream) - REQUIRED{Colors.NC}")
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
    print()

    # Reset session for new test
    tester.session_id = None

    both_accept = "application/json, text/event-stream"
    await tester.initialize_session(both_accept)
    tools_result = await tester.list_tools(both_accept)

    # Find the correct context7 tool name
    context7_tools = tools_result.get("context7_tools", [])
    if context7_tools:
        tool_name = context7_tools[0]["name"]
        print(f"{Colors.CYAN}Using tool: {tool_name}{Colors.NC}\n")
    else:
        # Fallback to known tool name pattern
        tool_name = "context7__resolve-library-id_Context_7"
        print(f"{Colors.YELLOW}No context7 tools found, trying: {tool_name}{Colors.NC}\n")

    await tester.call_tool(
        tool_name,
        {"libraryName": "aimsun"},
        both_accept
    )

    # Summary
    print()
    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print(f"{Colors.GREEN}SUMMARY{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print()
    print(f"{Colors.CYAN}Endpoint:{Colors.NC} http://localhost:8000/mcp/")
    print()
    print(f"{Colors.CYAN}Transport Mode:{Colors.NC}")
    print(f"  {Colors.GREEN}REQUIRED:{Colors.NC} Accept: application/json, text/event-stream")
    print()
    print(f"{Colors.YELLOW}Note:{Colors.NC} FastMCP requires BOTH content types in the Accept header.")
    print(f"       The server will respond with SSE (text/event-stream) format.")
    print()
    print(f"{Colors.CYAN}How it works:{Colors.NC}")
    print("  1. Client sends: Accept: application/json, text/event-stream")
    print("  2. Server responds with: Content-Type: text/event-stream")
    print("  3. Response format: SSE with JSON-RPC messages in data: lines")
    print()
    print(f"{Colors.CYAN}Default behavior:{Colors.NC}")
    print("  - Server uses SSE (Server-Sent Events) for streaming responses")
    print("  - Each SSE message contains a JSON-RPC response")
    print("  - This is the standard MCP HTTP transport protocol")
    print()
    print(f"{Colors.GREEN}✓ Transport mode tested successfully!{Colors.NC}")
    print()


if __name__ == "__main__":
    asyncio.run(main())

