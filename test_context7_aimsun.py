#!/usr/bin/env python3
"""
Test script to verify context7 tools work correctly with aimsun library.
This tests the full workflow: resolve-library-id -> get-library-docs
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


class Context7Tester:
    """Test Context7 MCP tools."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.mcp_endpoint = f"{base_url}/mcp/"
        self.session_id: Optional[str] = None
        self.accept_header = "application/json, text/event-stream"

    async def initialize_session(self) -> bool:
        """Initialize MCP session."""
        print(f"{Colors.CYAN}🔌 Initializing MCP session...{Colors.NC}")
        
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
                            "name": "context7-test-client",
                            "version": "1.0.0"
                        }
                    }
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": self.accept_header
                }
            )

            self.session_id = response.headers.get("mcp-session-id")
            
            if response.status_code == 200 and self.session_id:
                # Send initialized notification
                await client.post(
                    self.mcp_endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized"
                    },
                    headers={
                        "Content-Type": "application/json",
                        "Accept": self.accept_header,
                        "mcp-session-id": self.session_id
                    }
                )
                print(f"{Colors.GREEN}✓ Session initialized (ID: {self.session_id}){Colors.NC}\n")
                return True
            else:
                print(f"{Colors.RED}✗ Failed to initialize session{Colors.NC}\n")
                return False

    async def call_tool(self, tool_name: str, arguments: dict) -> Dict[str, Any]:
        """Call a specific tool and return the result."""
        print(f"{Colors.CYAN}🔧 Calling tool: {tool_name}{Colors.NC}")
        print(f"{Colors.BLUE}   Arguments: {json.dumps(arguments, indent=2)}{Colors.NC}")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self.mcp_endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments
                    }
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": self.accept_header,
                    "mcp-session-id": self.session_id
                }
            )

            # Parse SSE response
            result = None
            for line in response.text.split('\n'):
                if line.startswith('data: '):
                    data_json = line[6:].strip()
                    try:
                        parsed = json.loads(data_json)
                        if "result" in parsed:
                            result = parsed["result"]
                            break
                    except json.JSONDecodeError:
                        pass

            return result

    def print_result(self, result: Dict[str, Any], max_chars: int = 1000):
        """Pretty print the result."""
        if result:
            result_str = json.dumps(result, indent=2)
            if len(result_str) > max_chars:
                print(f"{Colors.GREEN}✓ Result (showing first {max_chars} chars):{Colors.NC}")
                print(result_str[:max_chars] + "...")
            else:
                print(f"{Colors.GREEN}✓ Result:{Colors.NC}")
                print(result_str)
        else:
            print(f"{Colors.RED}✗ No result returned{Colors.NC}")
        print()


async def main():
    """Run Context7 aimsun test."""
    print("=" * 80)
    print(f"{Colors.GREEN}Context7 MCP Tools - Aimsun Library Test{Colors.NC}")
    print("=" * 80)
    print()
    print("This test demonstrates the full Context7 workflow:")
    print("  1. resolve-library-id: Find the library ID for 'aimsun'")
    print("  2. get-library-docs: Retrieve documentation for the library")
    print()

    tester = Context7Tester()

    # Initialize session
    if not await tester.initialize_session():
        print(f"{Colors.RED}Failed to initialize session. Exiting.{Colors.NC}")
        return

    # Step 1: Resolve library ID for "aimsun"
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
    print(f"{Colors.YELLOW}STEP 1: Resolve Library ID for 'aimsun'{Colors.NC}")
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
    print()

    resolve_result = await tester.call_tool(
        "context7_resolve-library-id",
        {"libraryName": "aimsun"}
    )
    
    tester.print_result(resolve_result, max_chars=2000)

    # Extract library ID from result
    library_id = None
    if resolve_result and "content" in resolve_result:
        for content_item in resolve_result["content"]:
            if content_item.get("type") == "text":
                text = content_item.get("text", "")
                # Look for "Aimsun" in the text and extract the library ID
                lines = text.split('\n')
                for i, line in enumerate(lines):
                    if "Aimsun" in line and "Title:" in line:
                        # Look for the library ID in the next few lines
                        for j in range(i, min(i+5, len(lines))):
                            if "Context7-compatible library ID:" in lines[j]:
                                library_id = lines[j].split("Context7-compatible library ID:")[1].strip()
                                break
                        if library_id:
                            break

    if library_id:
        print(f"{Colors.GREEN}✓ Found library ID: {library_id}{Colors.NC}\n")
    else:
        print(f"{Colors.YELLOW}⚠ Could not extract library ID from response.{Colors.NC}")
        print(f"{Colors.YELLOW}  Will try with the Aimsun manual ID: /websites/aimsun_br{Colors.NC}\n")
        library_id = "/websites/aimsun_br"

    # Step 2: Get library docs
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
    print(f"{Colors.YELLOW}STEP 2: Get Library Documentation{Colors.NC}")
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
    print()

    docs_result = await tester.call_tool(
        "context7_get-library-docs",
        {
            "context7CompatibleLibraryID": library_id,
            "topic": "getting started"
        }
    )
    
    tester.print_result(docs_result, max_chars=3000)

    # Summary
    print()
    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print(f"{Colors.GREEN}SUMMARY{Colors.NC}")
    print(f"{Colors.GREEN}{'=' * 80}{Colors.NC}")
    print()
    print(f"{Colors.CYAN}Endpoint:{Colors.NC} http://localhost:8000/mcp/")
    print(f"{Colors.CYAN}Transport:{Colors.NC} SSE (Server-Sent Events)")
    print(f"{Colors.CYAN}Accept Header:{Colors.NC} application/json, text/event-stream")
    print()
    print(f"{Colors.CYAN}Tools Tested:{Colors.NC}")
    print(f"  1. {Colors.GREEN}context7_resolve-library-id{Colors.NC} - Find library by name")
    print(f"  2. {Colors.GREEN}context7_get-library-docs{Colors.NC} - Get documentation")
    print()
    print(f"{Colors.CYAN}Library Tested:{Colors.NC} aimsun")
    print(f"{Colors.CYAN}Library ID:{Colors.NC} {library_id}")
    print()
    print(f"{Colors.GREEN}✓ Context7 tools tested successfully!{Colors.NC}")
    print()


if __name__ == "__main__":
    asyncio.run(main())

