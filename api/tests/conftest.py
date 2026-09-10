"""Shared test fixtures for the API test suite."""

import pytest
from fastapi.testclient import TestClient

from bronze_api.main import app


@pytest.fixture
def client() -> TestClient:
    """A test client bound to the application."""
    return TestClient(app)
