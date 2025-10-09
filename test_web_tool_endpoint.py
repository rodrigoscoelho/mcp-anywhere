#!/usr/bin/env python3
"""Test the web UI tool test endpoint."""

import asyncio
import httpx
from http.cookies import SimpleCookie


async def main():
    """Test the tool endpoint."""
    base_url = "http://localhost:8000"

    # Server ID and Tool ID from the database check
    server_id = "6c335d0d"  # context7
    tool_id = "3e632b3d"    # resolve-library-id (without prefix in DB)

    # Create a cookie jar to persist cookies
    cookies = httpx.Cookies()

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0, cookies=cookies, follow_redirects=True) as client:
        # First, try to access the server page without login
        print("1. Accessing server page without login...")
        server_page = await client.get(f"/servers/{server_id}")
        print(f"   Status: {server_page.status_code}")
        print(f"   URL after redirects: {server_page.url}")
        print(f"   Is login page: {'login' in str(server_page.url)}")

        # Now login
        print("\n2. Logging in...")
        login_response = await client.post(
            "/auth/login",
            data={"username": "admin", "password": "Ropi1604mcp!"},
        )
        print(f"   Login status: {login_response.status_code}")
        print(f"   URL after login: {login_response.url}")
        print(f"   Cookies: {dict(client.cookies)}")

        # Test the tool
        print(f"\n3. Testing tool {tool_id} on server {server_id}...")
        print(f"   URL: /servers/{server_id}/tools/{tool_id}/test")
        print(f"   Data: libraryName=aimsun")

        test_response = await client.post(
            f"/servers/{server_id}/tools/{tool_id}/test",
            data={"libraryName": "aimsun"},
            headers={
                "HX-Request": "true",
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )

        print(f"\n4. Response:")
        print(f"   Status: {test_response.status_code}")
        print(f"   Content-Type: {test_response.headers.get('content-type')}")
        print(f"\n   Body (first 500 chars):")
        print(test_response.text[:500])


if __name__ == "__main__":
    asyncio.run(main())

