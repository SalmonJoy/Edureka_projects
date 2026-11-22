import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture
def client():
    # Each test starts with the same seeds and its own independent store.
    with TestClient(create_app()) as test_client:
        yield test_client
