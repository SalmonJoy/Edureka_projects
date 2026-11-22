import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture
def client():
    # No test can change another test's availability.
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def trip():
    return {
        "destination_id": "goa", "hotel_id": "goa-beach",
        "activity_ids": ["goa-walk"], "nights": 2, "rooms": 2, "travelers": 3,
        "food_total": 1000, "transport_total": 500, "misc_total": 200,
    }
