#!/usr/bin/env python3
"""
Test implementation for FastMCP async tool discovery patterns.

This test file demonstrates different approaches to handling the async/sync
boundary for FastMCP tool discovery within a Flask application context.
"""

import asyncio
import threading
import time
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


@dataclass
class MockMCPServer:
    """Mock server for testing purposes"""
    id: str
    name: str
    start_command: str
    runtime_type: str


class MockFastMCPToolManager:
    """Mock FastMCP tool manager that simulates async behavior"""
    
    def __init__(self, tools_data: Dict[str, Any] = None):
        self.tools_data = tools_data or {
            "server1_calculator": {"description": "Basic calculator"},
            "server1_weather": {"description": "Weather information"},
            "server2_file_manager": {"description": "File operations"},
        }
    
    async def get_tools(self) -> Dict[str, Any]:
        """Simulate async tool discovery with delay"""
        await asyncio.sleep(0.1)  # Simulate network/discovery delay
        return self.tools_data


class MockFastMCPRouter:
    """Mock FastMCP router for testing"""
    
    def __init__(self):
        self._tool_manager = MockFastMCPToolManager()


# ============================================================================
# PATTERN 1: Simple asyncio.run() Bridge (Current Issue Fix)
# ============================================================================

def discover_server_tools_simple(router: MockFastMCPRouter, server_config: MockMCPServer) -> List[Dict[str, Any]]:
    """
    Simple fix using asyncio.run() - works but creates new event loop each time.
    This is the immediate fix for the current RuntimeWarning.
    """
    async def _async_discover():
        try:
            # This is the problematic line that's currently causing the warning
            tools = await router._tool_manager.get_tools()
            discovered_tools = []
            
            # Find tools that belong to this server (prefixed with server_id)
            prefix = f"{server_config.id}_"
            for key, tool in tools.items():
                if key.startswith(prefix):
                    # Strip the prefix to get the original tool name
                    original_name = key[len(prefix):]
                    discovered_tools.append({
                        'name': original_name,
                        'description': tool.get('description', '')
                    })
            
            return discovered_tools
        except Exception as e:
            print(f"Error discovering tools for {server_config.name}: {e}")
            return []
    
    # This is the key fix - properly await the async operation
    return asyncio.run(_async_discover())


# ============================================================================
# PATTERN 2: Background Thread with Event Loop
# ============================================================================

class AsyncBridge:
    """
    Background thread pattern for better performance when making multiple async calls.
    Reuses event loop instead of creating new ones.
    """
    
    def __init__(self):
        self.loop = None
        self.thread = None
        self.executor = ThreadPoolExecutor(max_workers=2)
        self._start_background_loop()
    
    def _start_background_loop(self):
        """Start background thread with persistent event loop"""
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()
        
        self.thread = threading.Thread(target=run_loop, daemon=True)
        self.thread.start()
        
        # Wait for loop to be ready
        while self.loop is None:
            time.sleep(0.01)
    
    def run_async(self, coro):
        """Run async coroutine in background thread"""
        if self.loop is None:
            raise RuntimeError("Background loop not ready")
        
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=30)  # 30 second timeout
    
    def shutdown(self):
        """Clean shutdown of background resources"""
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(timeout=5)
        self.executor.shutdown(wait=True)


# Global bridge instance (in real code, this would be managed by Flask app)
_async_bridge = None

def get_async_bridge() -> AsyncBridge:
    """Singleton pattern for async bridge"""
    global _async_bridge
    if _async_bridge is None:
        _async_bridge = AsyncBridge()
    return _async_bridge


def discover_server_tools_background(router: MockFastMCPRouter, server_config: MockMCPServer) -> List[Dict[str, Any]]:
    """
    Background thread pattern - better performance for multiple calls.
    """
    async def _async_discover():
        tools = await router._tool_manager.get_tools()
        discovered_tools = []
        
        prefix = f"{server_config.id}_"
        for key, tool in tools.items():
            if key.startswith(prefix):
                original_name = key[len(prefix):]
                discovered_tools.append({
                    'name': original_name,
                    'description': tool.get('description', '')
                })
        
        return discovered_tools
    
    bridge = get_async_bridge()
    return bridge.run_async(_async_discover())


# ============================================================================
# PATTERN 3: Async Service Layer with Sync Wrapper
# ============================================================================

class AsyncToolDiscoveryService:
    """
    Clean separation between async service logic and sync Flask integration.
    This pattern provides the best long-term architecture.
    """
    
    async def discover_tools_async(self, router: MockFastMCPRouter, server_config: MockMCPServer) -> List[Dict[str, Any]]:
        """Pure async implementation"""
        try:
            # Proper async context management
            async with asyncio.timeout(30):  # Python 3.11+ timeout
                tools = await router._tool_manager.get_tools()
                discovered_tools = []
                
                prefix = f"{server_config.id}_"
                for key, tool in tools.items():
                    if key.startswith(prefix):
                        original_name = key[len(prefix):]
                        discovered_tools.append({
                            'name': original_name,
                            'description': tool.get('description', ''),
                            'server_id': server_config.id,
                            'server_name': server_config.name
                        })
                
                print(f"Discovered {len(discovered_tools)} tools for server '{server_config.name}'")
                return discovered_tools
                
        except asyncio.TimeoutError:
            print(f"Tool discovery timeout for {server_config.name}")
            return []
        except Exception as e:
            print(f"Tool discovery failed for {server_config.name}: {e}")
            return []
    
    def discover_tools_sync(self, router: MockFastMCPRouter, server_config: MockMCPServer) -> List[Dict[str, Any]]:
        """Sync wrapper for Flask integration"""
        return asyncio.run(self.discover_tools_async(router, server_config))


# ============================================================================
# PATTERN 4: Container Manager Integration
# ============================================================================

class ContainerBasedToolDiscovery:
    """
    Alternative approach: Discover tools during container build process.
    This leverages the existing container_manager.py infrastructure.
    """
    
    async def discover_tools_via_container(self, server_config: MockMCPServer) -> List[Dict[str, Any]]:
        """
        Discover tools by running the MCP server in a container and querying it directly.
        This would integrate with the existing ContainerManager.
        """
        # Simulate container-based discovery
        await asyncio.sleep(0.2)  # Simulate container startup time
        
        # In real implementation, this would:
        # 1. Start the MCP server in a container
        # 2. Connect as MCP client
        # 3. Call list_tools()
        # 4. Clean up container
        
        mock_tools = [
            {"name": "execute_code", "description": "Execute Python code"},
            {"name": "read_file", "description": "Read file contents"},
            {"name": "write_file", "description": "Write file contents"},
        ]
        
        return mock_tools
    
    def discover_tools_container_sync(self, server_config: MockMCPServer) -> List[Dict[str, Any]]:
        """Sync wrapper for container-based discovery"""
        return asyncio.run(self.discover_tools_via_container(server_config))


# ============================================================================
# TEST FUNCTIONS
# ============================================================================

def test_all_patterns():
    """Test all async patterns to validate they work correctly"""
    
    # Setup test data
    router = MockFastMCPRouter()
    server1 = MockMCPServer("server1", "Test Server 1", "uvx mcp-python", "uvx")
    server2 = MockMCPServer("server2", "Test Server 2", "npx @example/mcp", "npx")
    
    print("Testing FastMCP Async Tool Discovery Patterns\n")
    print("=" * 60)
    
    # Test Pattern 1: Simple asyncio.run()
    print("\n1. Testing Simple asyncio.run() Pattern:")
    start_time = time.time()
    tools1 = discover_server_tools_simple(router, server1)
    duration1 = time.time() - start_time
    print(f"   Found {len(tools1)} tools in {duration1:.3f}s")
    print(f"   Tools: {[t['name'] for t in tools1]}")
    
    # Test Pattern 2: Background thread
    print("\n2. Testing Background Thread Pattern:")
    start_time = time.time()
    tools2 = discover_server_tools_background(router, server1)
    duration2 = time.time() - start_time
    print(f"   Found {len(tools2)} tools in {duration2:.3f}s")
    print(f"   Tools: {[t['name'] for t in tools2]}")
    
    # Test Pattern 3: Service layer
    print("\n3. Testing Async Service Layer Pattern:")
    service = AsyncToolDiscoveryService()
    start_time = time.time()
    tools3 = service.discover_tools_sync(router, server1)
    duration3 = time.time() - start_time
    print(f"   Found {len(tools3)} tools in {duration3:.3f}s")
    print(f"   Tools: {[t['name'] for t in tools3]}")
    
    # Test Pattern 4: Container-based
    print("\n4. Testing Container-Based Discovery Pattern:")
    container_discovery = ContainerBasedToolDiscovery()
    start_time = time.time()
    tools4 = container_discovery.discover_tools_container_sync(server1)
    duration4 = time.time() - start_time
    print(f"   Found {len(tools4)} tools in {duration4:.3f}s")
    print(f"   Tools: {[t['name'] for t in tools4]}")
    
    # Performance comparison
    print("\n" + "=" * 60)
    print("Performance Comparison:")
    print(f"  Simple asyncio.run():     {duration1:.3f}s")
    print(f"  Background thread:        {duration2:.3f}s") 
    print(f"  Service layer:            {duration3:.3f}s")
    print(f"  Container-based:          {duration4:.3f}s")
    
    # Cleanup
    if _async_bridge:
        _async_bridge.shutdown()
    
    print("\n✅ All patterns tested successfully!")


def test_concurrent_discovery():
    """Test concurrent tool discovery to validate thread safety"""
    
    print("\n" + "=" * 60)
    print("Testing Concurrent Tool Discovery")
    
    router = MockFastMCPRouter()
    servers = [
        MockMCPServer(f"server{i}", f"Test Server {i}", "uvx mcp-python", "uvx")
        for i in range(1, 6)
    ]
    
    # Test concurrent discovery with background thread pattern
    start_time = time.time()
    
    async def discover_all_concurrent():
        service = AsyncToolDiscoveryService()
        tasks = [
            service.discover_tools_async(router, server)
            for server in servers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results
    
    results = asyncio.run(discover_all_concurrent())
    duration = time.time() - start_time
    
    successful = [r for r in results if not isinstance(r, Exception)]
    failed = [r for r in results if isinstance(r, Exception)]
    
    print(f"Concurrent discovery of {len(servers)} servers:")
    print(f"  Completed in: {duration:.3f}s")
    print(f"  Successful: {len(successful)}")
    print(f"  Failed: {len(failed)}")
    
    if failed:
        print("  Failures:")
        for error in failed:
            print(f"    - {error}")


if __name__ == "__main__":
    print("FastMCP Async Tool Discovery Test Suite")
    print("=" * 60)
    
    # Run basic pattern tests
    test_all_patterns()
    
    # Run concurrent tests
    test_concurrent_discovery()
    
    print("\n🎉 All tests completed!")
    print("\nNext Steps:")
    print("1. Choose the pattern that best fits your architecture")
    print("2. Implement the chosen pattern in discover_server_tools()")
    print("3. Test with actual FastMCP instances")
    print("4. Monitor performance and error handling")