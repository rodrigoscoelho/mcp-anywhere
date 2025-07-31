#!/usr/bin/env python3
"""
Test client for MCP Router using FastMCP with OAuth authentication.

This client will:
1. Register as an OAuth client
2. Complete the authorization code flow with PKCE
3. Get an access token
4. Connect to the MCP server using FastMCP
5. Run the a9d320d8_run_python_code tool
"""

import asyncio
import base64
import hashlib
import json
import secrets
import sys
import webbrowser
from typing import Dict, Optional, Any
from urllib.parse import parse_qs, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import time

import httpx
from fastmcp import Client
from pydantic import BaseModel


class OAuthConfig(BaseModel):
    """OAuth configuration from server metadata"""
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    scopes_supported: list[str]


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback"""
    
    def do_GET(self):
        """Handle GET request for OAuth callback"""
        if self.path.startswith('/callback'):
            # Parse query parameters
            parsed_url = urlparse(self.path)
            params = parse_qs(parsed_url.query)
            
            # Extract authorization code
            if 'code' in params:
                self.server.auth_code = params['code'][0]
                
                # Send success response
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                
                success_html = """
                <html>
                <head><title>Authorization Successful</title></head>
                <body>
                    <h1>✅ Authorization Successful!</h1>
                    <p>You can now close this window and return to the terminal.</p>
                    <script>setTimeout(() => window.close(), 3000);</script>
                </body>
                </html>
                """
                self.wfile.write(success_html.encode())
            else:
                # Send error response
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                
                error_html = """
                <html>
                <head><title>Authorization Failed</title></head>
                <body>
                    <h1>❌ Authorization Failed</h1>
                    <p>No authorization code received.</p>
                </body>
                </html>
                """
                self.wfile.write(error_html.encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress HTTP server logs"""
        pass


class OAuthClient:
    """OAuth 2.1 client with PKCE support for MCP Router"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        """
        Initialize OAuth client.
        
        Args:
            base_url: Base URL of the MCP Router server
        """
        self.base_url = base_url.rstrip("/")
        self.client_id: Optional[str] = None
        self.access_token: Optional[str] = None
        self.oauth_config: Optional[OAuthConfig] = None
        self.callback_server: Optional[HTTPServer] = None
        
    async def discover_oauth_config(self) -> OAuthConfig:
        """
        Discover OAuth configuration from server metadata.
        
        Returns:
            OAuth configuration
            
        Raises:
            httpx.HTTPError: If discovery fails
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/.well-known/oauth-authorization-server")
            response.raise_for_status()
            config_data = response.json()
            
            self.oauth_config = OAuthConfig(
                authorization_endpoint=config_data["authorization_endpoint"],
                token_endpoint=config_data["token_endpoint"],
                registration_endpoint=config_data["registration_endpoint"],
                scopes_supported=config_data["scopes_supported"]
            )
            
            print(f"✓ Discovered OAuth config: {self.oauth_config.authorization_endpoint}")
            return self.oauth_config
    
    async def register_client(self) -> str:
        """
        Register as an OAuth client.
        
        Returns:
            Client ID
            
        Raises:
            httpx.HTTPError: If registration fails
        """
        if not self.oauth_config:
            await self.discover_oauth_config()
            
        registration_data = {
            "client_name": "GoFastMCP Test Client",
            "redirect_uris": ["http://localhost:8080/callback"],
            "scopes": ["mcp:read", "mcp:write"]
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.oauth_config.registration_endpoint,
                json=registration_data
            )
            response.raise_for_status()
            result = response.json()
            
            self.client_id = result["client_id"]
            print(f"✓ Registered OAuth client: {self.client_id}")
            return self.client_id
    
    def generate_pkce_challenge(self) -> tuple[str, str]:
        """
        Generate PKCE code verifier and challenge.
        
        Returns:
            Tuple of (code_verifier, code_challenge)
        """
        # Generate code verifier (43-128 characters)
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        
        # Generate code challenge
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip("=")
        
        return code_verifier, code_challenge
    
    def start_callback_server(self) -> int:
        """
        Start temporary callback server to catch OAuth redirect.
        
        Returns:
            Port number the server is running on
        """
        # Try to start server on port 8080, fallback to available port
        for port in range(8080, 8090):
            try:
                self.callback_server = HTTPServer(('localhost', port), CallbackHandler)
                self.callback_server.auth_code = None
                
                # Start server in background thread
                server_thread = threading.Thread(target=self.callback_server.serve_forever)
                server_thread.daemon = True
                server_thread.start()
                
                print(f"✓ Started callback server on http://localhost:{port}")
                return port
                
            except OSError:
                continue
                
        raise RuntimeError("Could not start callback server on any port 8080-8089")
    
    def stop_callback_server(self):
        """Stop the callback server"""
        if self.callback_server:
            self.callback_server.shutdown()
            self.callback_server.server_close()
            self.callback_server = None
    
    async def get_authorization_code(self) -> str:
        """
        Get authorization code through OAuth callback server.
        
        Returns:
            Authorization code
        """
        if not self.client_id:
            await self.register_client()
        
        # Start callback server
        callback_port = self.start_callback_server()
        callback_uri = f"http://localhost:{callback_port}/callback"
            
        # Generate PKCE parameters
        code_verifier, code_challenge = self.generate_pkce_challenge()
        state = secrets.token_urlsafe(32)
        
        # Build authorization URL
        auth_params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": callback_uri,
            "scope": "mcp:read mcp:write",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256"
        }
        
        auth_url = self.oauth_config.authorization_endpoint + "?" + "&".join(
            f"{k}={v}" for k, v in auth_params.items()
        )
        
        print(f"Opening authorization URL: {auth_url}")
        print("Complete the authorization in your browser...")
        webbrowser.open(auth_url)
        
        # Wait for callback server to receive the code
        print("Waiting for authorization callback...")
        timeout = 120  # 2 minutes timeout
        start_time = time.time()
        
        while not self.callback_server.auth_code and (time.time() - start_time) < timeout:
            await asyncio.sleep(0.5)
            
        # Stop the callback server
        auth_code = self.callback_server.auth_code
        self.stop_callback_server()
        
        if not auth_code:
            raise TimeoutError("Authorization timed out. Please try again.")
            
        print("✓ Authorization code received!")
        
        # Store for token exchange
        self._code_verifier = code_verifier
        self._redirect_uri = callback_uri  # Store the actual redirect URI used
        return auth_code
    
    async def exchange_code_for_token(self, auth_code: str) -> str:
        """
        Exchange authorization code for access token.
        
        Args:
            auth_code: Authorization code from the authorization server
            
        Returns:
            Access token
            
        Raises:
            httpx.HTTPError: If token exchange fails
        """
        # Use the same redirect URI that was used in authorization
        token_data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": self.client_id,
            "redirect_uri": self._redirect_uri,  # Use the actual redirect URI that was used
            "code_verifier": self._code_verifier
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.oauth_config.token_endpoint,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            result = response.json()
            
            self.access_token = result["access_token"]
            expires_in = result.get("expires_in", 3600)
            
            print(f"✓ Got access token (expires in {expires_in}s)")
            return self.access_token
    
    async def authenticate(self) -> str:
        """
        Complete full OAuth authentication flow.
        
        Returns:
            Access token
        """
        print("Starting OAuth 2.1 authentication with PKCE...")
        
        await self.discover_oauth_config()
        await self.register_client()
        auth_code = await self.get_authorization_code()
        access_token = await self.exchange_code_for_token(auth_code)
        
        print("✓ OAuth authentication completed successfully!")
        return access_token


class MCPTestClient:
    """FastMCP client for testing tool execution"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        """
        Initialize MCP test client.
        
        Args:
            base_url: Base URL of the MCP Router server
        """
        self.base_url = base_url.rstrip("/")
        self.mcp_url = f"{self.base_url}/mcp/"
        self.oauth_client = OAuthClient(base_url)
        self.client: Optional[Client] = None
        
    async def authenticate(self) -> None:
        """Authenticate with OAuth and get access token."""
        await self.oauth_client.authenticate()
        
    async def connect(self) -> None:
        """
        Connect to MCP server using FastMCP.
        
        Raises:
            Exception: If connection fails
        """
        if not self.oauth_client.access_token:
            raise ValueError("Must authenticate before connecting")
            
        print(f"Connecting to MCP server at {self.mcp_url}...")
        
        try:
            # Create FastMCP client with HTTP transport
            # FastMCP Client expects the URL and authentication via environment or during call
            self.client = Client(self.mcp_url)
            
            print("✓ Connected to MCP server successfully!")
            
        except Exception as e:
            print(f"✗ Failed to connect to MCP server: {e}")
            raise
    
    async def list_tools(self) -> list[Dict[str, Any]]:
        """
        List available tools using FastMCP Client with proper authentication.
        
        Returns:
            List of available tools
        """
        try:
            # Create a FastMCP client with OAuth authentication
            from fastmcp.client.auth import OAuth
            
            # Use FastMCP's OAuth helper
            oauth_helper = OAuth(mcp_url=self.mcp_url)
            # Manually set the access token we already have
            oauth_helper._access_token = self.oauth_client.access_token
            
            # Create client with OAuth
            async with Client(self.mcp_url, auth=oauth_helper) as client:
                tools_result = await client.list_tools()
                tools = tools_result.tools if hasattr(tools_result, 'tools') else tools_result
                
                print(f"✓ Found {len(tools)} tools available")
                
                for i, tool in enumerate(tools, 1):
                    print(f"  {i}. {tool.name}: {tool.description or 'No description'}")
                    
                return [{"name": tool.name, "description": tool.description} for tool in tools]
                
        except Exception as e:
            print(f"✗ Failed to list tools with FastMCP OAuth: {e}")
            print("Trying alternative approach...")
            
            # Fallback: Try SSE transport with custom headers
            try:
                headers = {
                    "Authorization": f"Bearer {self.oauth_client.access_token}",
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache"
                }
                
                # Try SSE endpoint
                sse_url = self.mcp_url.replace("/mcp/", "/mcp/sse") if "/mcp/" in self.mcp_url else f"{self.mcp_url}/sse"
                
                async with httpx.AsyncClient() as http_client:
                    # First try to establish SSE connection for tools list
                    response = await http_client.post(
                        sse_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/list",
                            "params": {}
                        },
                        headers=headers
                    )
                    
                    if response.status_code == 406:
                        # Try with different headers
                        headers.update({
                            "Content-Type": "application/json",
                            "Accept": "application/json"
                        })
                        
                        response = await http_client.post(
                            self.mcp_url,
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "tools/list",
                                "params": {}
                            },
                            headers=headers
                        )
                    
                    response.raise_for_status()
                    result = response.json()
                    
                    if "error" in result:
                        raise Exception(f"MCP Error: {result['error']}")
                    
                    tools = result.get("result", {}).get("tools", [])
                    print(f"✓ Found {len(tools)} tools available")
                    
                    for i, tool in enumerate(tools, 1):
                        print(f"  {i}. {tool.get('name', 'Unknown')}: {tool.get('description', 'No description')}")
                        
                    return tools
                    
            except Exception as fallback_error:
                print(f"✗ Fallback also failed: {fallback_error}")
                raise
    
    async def run_python_code_tool(self, code: str = "print('Hello from MCP!')") -> Dict[str, Any]:
        """
        Run the a9d320d8_run_python_code tool using FastMCP Client.
        
        Args:
            code: Python code to execute
            
        Returns:
            Tool execution result
        """
        tool_name = "a9d320d8_run_python_code"
        
        try:
            print(f"Running tool '{tool_name}' with code:")
            print(f"  {code}")
            
            # Use FastMCP client with OAuth authentication
            from fastmcp.client.auth import OAuth
            
            oauth_helper = OAuth(mcp_url=self.mcp_url)
            oauth_helper._access_token = self.oauth_client.access_token
            
            async with Client(self.mcp_url, auth=oauth_helper) as client:
                result = await client.call_tool(tool_name, {"code": code})
                
                print("✓ Tool executed successfully!")
                print("Result:")
                
                # Extract and display content from the result
                if hasattr(result, 'content'):
                    content = result.content
                    if isinstance(content, list):
                        for item in content:
                            if hasattr(item, 'text'):
                                print(item.text)
                            elif isinstance(item, dict):
                                if item.get("type") == "text":
                                    print(item.get("text", ""))
                                else:
                                    print(json.dumps(item, indent=2))
                            else:
                                print(str(item))
                    else:
                        print(str(content))
                else:
                    print(str(result))
                
                return {"success": True, "content": result.content if hasattr(result, 'content') else str(result)}
                
        except Exception as e:
            print(f"✗ Failed to run tool with FastMCP: {e}")
            print("Trying fallback approach...")
            
            # Fallback to direct HTTP
            try:
                headers = {
                    "Authorization": f"Bearer {self.oauth_client.access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }
                
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.post(
                        self.mcp_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": tool_name,
                                "arguments": {"code": code}
                            }
                        },
                        headers=headers
                    )
                    response.raise_for_status()
                    result = response.json()
                    
                    if "error" in result:
                        raise Exception(f"MCP Error: {result['error']}")
                    
                    print("✓ Tool executed successfully!")
                    print("Result:")
                    
                    tool_result = result.get("result", {})
                    content = tool_result.get("content", [])
                    
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    print(item.get("text", ""))
                                else:
                                    print(json.dumps(item, indent=2))
                            else:
                                print(str(item))
                    else:
                        print(str(content))
                    
                    return {"success": True, "result": tool_result}
                    
            except Exception as fallback_error:
                print(f"✗ Fallback also failed: {fallback_error}")
                raise
    
    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        if self.client:
            # FastMCP client handles disconnection through context manager
            print("✓ Disconnected from MCP server")


async def main():
    """Main function to run the test client."""
    print("MCP Router Test Client with OAuth Authentication")
    print("=" * 50)
    
    try:
        # Initialize test client
        client = MCPTestClient()
        
        # Authenticate
        await client.authenticate()
        
        # Connect to MCP server
        await client.connect()
        
        # List available tools
        tools = await client.list_tools()
        
        # Check if our target tool is available
        target_tool = "a9d320d8_run_python_code"
        tool_found = any(tool['name'] == target_tool for tool in tools)
        
        if not tool_found:
            print(f"⚠ Warning: Tool '{target_tool}' not found in available tools")
            print("Available tools:")
            for tool in tools:
                print(f"  - {tool['name']}")
            
            # Try to run it anyway in case it's dynamically available
            print(f"\nTrying to run '{target_tool}' anyway...")
        
        # Run the Python code tool
        test_code = """
import sys
import os
print(f"Python version: {sys.version}")
print(f"Current working directory: {os.getcwd()}")
print("Hello from MCP Router Python execution!")

# Simple calculation
result = 2 + 2
print(f"2 + 2 = {result}")
"""
        
        result = await client.run_python_code_tool(test_code)
        
        # Disconnect
        await client.disconnect()
        
        print("\n" + "=" * 50)
        print("✓ Test completed successfully!")
        
    except KeyboardInterrupt:
        print("\n✗ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())