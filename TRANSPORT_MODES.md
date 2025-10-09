# MCP Anywhere - Transport Modes Guide

## Overview

MCP Anywhere supports **SSE (Server-Sent Events)** as the transport protocol for the MCP HTTP endpoint. This document explains how to use the endpoint and what to expect.

## Endpoint

**MCP Endpoint:** `http://localhost:8000/mcp/`

The endpoint path is configurable via the `MCP_PATH` environment variable (default: `/mcp`).

## Transport Protocol

### Required Accept Header

**IMPORTANT:** FastMCP requires **BOTH** content types in the Accept header:

```
Accept: application/json, text/event-stream
```

### Why Both?

- FastMCP validates that clients can handle both JSON and SSE responses
- The server will respond with `406 Not Acceptable` if only one content type is specified
- This ensures compatibility with the MCP protocol specification

### Response Format

When you send a request with the correct Accept header:

1. **Request:**
   ```
   Accept: application/json, text/event-stream
   ```

2. **Response:**
   ```
   Content-Type: text/event-stream
   ```

3. **Format:** SSE (Server-Sent Events) with JSON-RPC messages

## How SSE Works

### SSE Response Structure

```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{...}}

```

Each SSE message:
- Starts with `event: message`
- Contains a `data:` line with the JSON-RPC response
- Ends with a blank line

### Parsing SSE Responses

To parse SSE responses:

1. Split the response by newlines
2. Look for lines starting with `data: `
3. Extract JSON from each data line (remove `data: ` prefix)
4. Parse the JSON to get the JSON-RPC response

**Example (Python):**

```python
for line in response.text.split('\n'):
    if line.startswith('data: '):
        data_json = line[6:].strip()
        parsed = json.loads(data_json)
        # Use parsed JSON-RPC response
```

## MCP Protocol Handshake

To properly communicate with the MCP endpoint, follow this sequence:

### 1. Initialize Session

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

**IMPORTANT:** Save the `mcp-session-id` from the response headers!

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

## Example: Calling a Tool

### List Available Tools

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

### Call a Specific Tool

Example: Using Context7 to search for "aimsun" library:

```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "context7_resolve-library-id",
      "arguments": {
        "libraryName": "aimsun"
      }
    }
  }'
```

## Testing Scripts

We provide test scripts to verify the transport mode:

### 1. Transport Mode Test

```bash
uv run python test_transport_modes.py
```

This script tests:
- Session initialization
- Tool listing
- Basic tool calls

### 2. Context7 Aimsun Test

```bash
uv run python test_context7_aimsun.py
```

This script demonstrates:
- Full Context7 workflow
- Resolving library ID for "aimsun"
- Retrieving documentation

## Common Issues

### Issue: "Not Acceptable: Client must accept both application/json and text/event-stream"

**Solution:** Include both content types in the Accept header:
```
Accept: application/json, text/event-stream
```

### Issue: "Bad Request: No valid session ID provided"

**Solution:** Include the `mcp-session-id` header from the initialize response in all subsequent requests.

### Issue: "Invalid request parameters" on tools/list

**Solution:** Make sure you:
1. Called `initialize` first
2. Sent `notifications/initialized`
3. Are using the correct `mcp-session-id` header

## Summary

✅ **Transport Mode:** SSE (Server-Sent Events)  
✅ **Required Accept Header:** `application/json, text/event-stream`  
✅ **Response Format:** SSE with JSON-RPC messages  
✅ **Default Behavior:** Server always responds with SSE format  
✅ **Session Management:** Required via `mcp-session-id` header  

## References

- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [Server-Sent Events (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
- [SSE Support Documentation](docs/SSE_SUPPORT.md)

