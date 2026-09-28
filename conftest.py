"""Pytest configuration for async tests."""
import pytest
import asyncio

# Configure pytest to work with asyncio
@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
