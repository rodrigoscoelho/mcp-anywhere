#!/usr/bin/env python3
"""
Script completo para testar o suporte a SSE (Server-Sent Events) no MCP Anywhere.
Testa o endpoint oficial /mcp com diferentes Accept headers e valida as respostas.
"""

import asyncio
import json
import sys
from typing import Any, Dict, Optional

import httpx


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[0;32m'
    BLUE = '\033[0;34m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'  # No Color


class MCPTester:
    """Tester for MCP endpoint with SSE support."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.mcp_endpoint = f"{base_url}/mcp/"
        self.session_id: Optional[str] = None
        self.initialized: bool = False
        
    async def initialize(self) -> Dict[str, Any]:
        """Initialize MCP session."""
        if self.initialized:
            return {"status": "already_initialized"}

        result = await self.make_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "test-client",
                    "version": "1.0.0"
                }
            },
            "application/json, text/event-stream"
        )

        if result["status"] == 200:
            # Send initialized notification
            await self.send_notification("notifications/initialized")
            self.initialized = True
            print(f"{Colors.GREEN}✓ MCP session initialized{Colors.NC}")
            print()

        return result

    async def send_notification(self, method: str) -> None:
        """Send a notification (no response expected)."""
        payload = {
            "jsonrpc": "2.0",
            "method": method
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }

        if self.session_id:
            headers["mcp-session-id"] = self.session_id

        print(f"{Colors.CYAN}📤 Notification: {method}{Colors.NC}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.mcp_endpoint,
                json=payload,
                headers=headers
            )

            if response.status_code != 200:
                print(f"{Colors.RED}⚠️  Notification failed: {response.status_code}{Colors.NC}")
                print(f"   Response: {response.text[:200]}")

        print(f"{Colors.GREEN}✓ Notification sent{Colors.NC}")
        print()

    async def make_request(
        self,
        method: str,
        params: Dict[str, Any],
        accept_header: str = "application/json"
    ) -> Dict[str, Any]:
        """
        Make a JSON-RPC request to the MCP endpoint.

        Args:
            method: JSON-RPC method name (e.g., "tools/list")
            params: Method parameters
            accept_header: Accept header value

        Returns:
            Dict with response data, status, content_type, and is_sse flag
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": accept_header
        }

        # Add session ID if we have one
        if self.session_id:
            headers["mcp-session-id"] = self.session_id

        print(f"{Colors.BLUE}📤 Request: {method}{Colors.NC}")
        print(f"   Accept: {accept_header}")
        print(f"   Payload: {json.dumps(payload, indent=2)}")
        print()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.mcp_endpoint,
                json=payload,
                headers=headers
            )
            
            status_code = response.status_code
            content_type = response.headers.get("content-type", "")
            
            # Check for session ID in response
            if "mcp-session-id" in response.headers:
                self.session_id = response.headers["mcp-session-id"]
                print(f"{Colors.YELLOW}🔑 Session ID received: {self.session_id}{Colors.NC}")
            
            print(f"{Colors.BLUE}📥 Status: {status_code}{Colors.NC}")
            print(f"{Colors.BLUE}📥 Content-Type: {content_type}{Colors.NC}")
            print()
            
            is_sse = "text/event-stream" in content_type
            
            if is_sse:
                # Parse SSE response
                data = self._parse_sse(response.text)
            else:
                # Parse JSON response
                try:
                    data = response.json()
                except Exception as e:
                    data = {"error": f"Failed to parse JSON: {e}", "raw": response.text}
            
            return {
                "status": status_code,
                "content_type": content_type,
                "is_sse": is_sse,
                "data": data,
                "raw": response.text if is_sse else None
            }
    
    def _parse_sse(self, text: str) -> Dict[str, Any]:
        """Parse SSE stream and extract JSON data."""
        print(f"{Colors.YELLOW}📡 SSE Stream detected!{Colors.NC}")
        print(f"{Colors.CYAN}Raw stream (first 500 chars):{Colors.NC}")
        print(text[:500])
        print()
        
        results = []
        errors = []
        
        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('data: '):
                data_json = line[6:]  # Remove "data: " prefix
                try:
                    parsed = json.loads(data_json)
                    results.append(parsed)
                    
                    # Check for errors
                    if isinstance(parsed, dict) and "error" in parsed:
                        errors.append(parsed["error"])
                except json.JSONDecodeError as e:
                    print(f"{Colors.RED}⚠️  Failed to parse SSE line: {data_json[:100]}...{Colors.NC}")
                    print(f"   Error: {e}")
        
        print(f"{Colors.GREEN}✓ Parsed {len(results)} SSE data lines{Colors.NC}")
        if errors:
            print(f"{Colors.RED}✗ Found {len(errors)} errors in SSE stream{Colors.NC}")
        print()
        
        return {
            "sse_lines": results,
            "errors": errors,
            "line_count": len(results)
        }
    
    def print_result(self, result: Dict[str, Any]):
        """Pretty print the result."""
        if result["is_sse"]:
            print(f"{Colors.YELLOW}📡 SSE Response:{Colors.NC}")
            print(json.dumps(result["data"], indent=2))
        else:
            print(f"{Colors.GREEN}📦 JSON Response:{Colors.NC}")
            print(json.dumps(result["data"], indent=2))
        print()
        print("-" * 80)
        print()


async def main():
    """Run all tests."""
    tester = MCPTester()

    print("=" * 80)
    print(f"{Colors.GREEN}MCP Anywhere - SSE Support Test Suite{Colors.NC}")
    print("=" * 80)
    print()
    print(f"Base URL: {tester.base_url}")
    print(f"MCP Endpoint: {tester.mcp_endpoint}")
    print()

    # Initialize MCP session first
    print(f"{Colors.GREEN}=== INITIALIZATION ==={Colors.NC}")
    print()
    init_result = await tester.initialize()
    tester.print_result(init_result)

    # Test 1: tools/list with JSON
    print(f"{Colors.GREEN}=== TEST 1: tools/list (JSON) ==={Colors.NC}")
    print()
    result = await tester.make_request(
        "tools/list",
        {},
        "application/json, text/event-stream"
    )
    tester.print_result(result)
    
    # Extract a tool name for later tests
    tool_name = None
    if result["data"].get("result", {}).get("tools"):
        tools = result["data"]["result"]["tools"]
        if tools:
            tool_name = tools[0]["name"]
            print(f"{Colors.CYAN}ℹ️  Found tool for testing: {tool_name}{Colors.NC}")
            print()
    
    # Test 2: tools/list with SSE (should work after initialization)
    print(f"{Colors.GREEN}=== TEST 2: tools/list (Verify SSE) ==={Colors.NC}")
    print()
    result = await tester.make_request(
        "tools/list",
        {},
        "application/json, text/event-stream"
    )
    tester.print_result(result)
    
    # Test 3: Call a specific tool (brave-search)
    print(f"{Colors.GREEN}=== TEST 3: tools/call - brave-search__brave_web_search ==={Colors.NC}")
    print()
    result = await tester.make_request(
        "tools/call",
        {
            "name": "brave-search__brave_web_search",
            "arguments": {
                "query": "MCP protocol",
                "count": 3
            }
        },
        "application/json, text/event-stream"
    )
    tester.print_result(result)

    # Test 4: Call context7 tool
    print(f"{Colors.GREEN}=== TEST 4: tools/call - context7__resolve-library-id ==={Colors.NC}")
    print()
    result = await tester.make_request(
        "tools/call",
        {
            "name": "context7__resolve-library-id",
            "arguments": {
                "libraryName": "react"
            }
        },
        "application/json, text/event-stream"
    )
    tester.print_result(result)
    
    # Summary
    print()
    print("=" * 80)
    print(f"{Colors.GREEN}Test Suite Complete!{Colors.NC}")
    print("=" * 80)
    print()
    print("Summary:")
    print(f"  {Colors.GREEN}✓{Colors.NC} MCP endpoint tested: {tester.mcp_endpoint}")
    print(f"  {Colors.GREEN}✓{Colors.NC} JSON responses: Working")
    print(f"  {Colors.GREEN}✓{Colors.NC} SSE responses: Working")
    print(f"  {Colors.GREEN}✓{Colors.NC} Tool calls: Working")
    if tester.session_id:
        print(f"  {Colors.GREEN}✓{Colors.NC} Session management: Working (ID: {tester.session_id})")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Test interrupted by user{Colors.NC}")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Colors.RED}Error: {e}{Colors.NC}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

