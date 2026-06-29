"""
Pytest configuration for AXIOM tests.

Provides shared fixtures and configures the async test mode.
"""

from __future__ import annotations

import pytest


# Ensure asyncio event loop is properly scoped
@pytest.fixture(scope="session")
def event_loop_policy():
    """Use the default event loop policy."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()
