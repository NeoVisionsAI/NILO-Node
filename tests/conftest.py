"""Shared pytest fixtures."""

from __future__ import annotations

import asyncio

import pytest

from nilo_node.sources import registry


@pytest.fixture(autouse=True)
def _reset_plugin_singletons() -> None:
    yield
    registry._camera_manager = None
    registry._bluetooth_manager = None


@pytest.fixture(autouse=True)
def _isolated_event_loop() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()
    asyncio.set_event_loop(asyncio.new_event_loop())
