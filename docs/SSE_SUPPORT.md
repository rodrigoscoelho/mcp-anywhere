# SSE (Server-Sent Events) Support in MCP Anywhere

## Overview

MCP Anywhere fully supports **Server-Sent Events (SSE)** for the MCP HTTP transport protocol. This document explains how SSE works, how to test it, and the proper MCP handshake sequence.

## What is SSE?

Server-Sent Events (SSE) is a server push technology enabling a server to send automatic updates to a client via HTTP connection. In the context of MCP (Model Context Protocol), SSE is used to stream responses from the server to the client.

## MCP Protocol Handshake

To properly communicate with the MCP endpoint, clients must follow this sequence:

### 1. Initialize Session

**Request:**
```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {
        "name": "my-client",
        "version": "1.0.0"
      }
    }
  }'
```

**Response:**
```
HTTP/1.1 200 OK
Content-Type: text/event-stream
mcp-session-id: <session-id>

event: message
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",...}}
```

**Important:** Save the `mcp-session-id` from the response headers!

### 2. Send Initialized Notification

After receiving the initialize response, send the initialized notification:

```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "method": "notifications/initialized"
  }'
```

### 3. Use MCP Methods

Now you can use any MCP method (tools/list, tools/call, etc.):

```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'
```

## Accept Header Requirements

The MCP endpoint supports both JSON and SSE responses. The `Accept` header determines the response format:

- **JSON only:** `Accept: application/json` (may return 406 Not Acceptable)
- **SSE only:** `Accept: text/event-stream` (may return 406 Not Acceptable)
- **Both (recommended):** `Accept: application/json, text/event-stream` ✅

**Best Practice:** Always include both content types in the Accept header to ensure compatibility.

## SSE Response Format

SSE responses follow this format:

```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{...}}

```

Each SSE message:
- Starts with `event: message`
- Contains a `data:` line with JSON-RPC response
- Ends with a blank line

## Testing SSE Support

### Using the Test Scripts

We provide two test scripts:

#### 1. Python Test Script (Recommended)

```bash
uv run python test_mcp_sse_complete.py
```

This script:
- ✅ Performs proper MCP handshake (initialize → initialized → methods)
- ✅ Tests SSE parsing
- ✅ Tests tool listing and calling
- ✅ Validates session management
- ✅ Provides colored output for easy debugging

#### 2. Bash Test Script

```bash
./test_mcp_sse_official.sh
```

This script:
- ✅ Tests different Accept header combinations
- ✅ Tests tool calls with real MCP servers
- ✅ Uses curl for direct HTTP testing

### Manual Testing with curl

#### Complete Example

```bash
# 1. Initialize
RESPONSE=$(curl -s -D - -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "test", "version": "1.0"}
    }
  }')

# Extract session ID
SESSION_ID=$(echo "$RESPONSE" | grep -i "mcp-session-id:" | cut -d' ' -f2 | tr -d '\r')

echo "Session ID: $SESSION_ID"

# 2. Send initialized notification
curl -s -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3. List tools
curl -s -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# 4. Call a tool
curl -s -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION_ID" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "brave-search__brave_web_search",
      "arguments": {"query": "MCP protocol", "count": 3}
    }
  }'
```

## Implementation Details

### Server-Side

The MCP endpoint is implemented using FastMCP 2.11.2, which provides built-in SSE support:

- **Endpoint:** `/mcp/` (configurable via `MCP_PATH` environment variable)
- **Transport:** HTTP with SSE streaming
- **Session Management:** Automatic via `mcp-session-id` header
- **Content Negotiation:** Supports both JSON and SSE based on Accept header

### Client-Side Parsing

When parsing SSE responses:

1. Split response by newlines
2. Look for lines starting with `data: `
3. Extract JSON from each data line (remove `data: ` prefix)
4. Parse JSON to get the JSON-RPC response

Example Python code:

```python
for line in response.text.split('\n'):
    line = line.strip()
    if line.startswith('data: '):
        data_json = line[6:]  # Remove "data: " prefix
        parsed = json.loads(data_json)
        # Process parsed JSON-RPC response
```

## Common Issues and Solutions

### Issue: "Not Acceptable: Client must accept both application/json and text/event-stream"

**Solution:** Include both content types in Accept header:
```
Accept: application/json, text/event-stream
```

### Issue: "Invalid request parameters" on tools/list

**Solution:** Make sure you:
1. Called `initialize` first
2. Sent `notifications/initialized`
3. Are using the correct `mcp-session-id` header

### Issue: "Bad Request: No valid session ID provided"

**Solution:** Include the `mcp-session-id` header from the initialize response in all subsequent requests.

## Verification

To verify SSE support is working:

```bash
# Run the complete test suite
uv run python test_mcp_sse_complete.py

# Expected output:
# ✓ MCP session initialized
# ✓ SSE Stream detected!
# ✓ Parsed N SSE data lines
# ✓ Tools listed successfully
# ✓ Tool calls working
```

## References

- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [Server-Sent Events (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)

## Summary

✅ **SSE is fully supported** in MCP Anywhere  
✅ **Proper MCP handshake** is required (initialize → initialized → methods)  
✅ **Session management** works correctly  
✅ **Both JSON and SSE** responses are supported  
✅ **Test scripts** are provided for validation  

The implementation follows the MCP specification and works with standard MCP clients.

