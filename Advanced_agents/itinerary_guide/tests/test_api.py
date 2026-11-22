import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


DESTINATION_IDS = [
    "goa", "jaipur", "manali", "udaipur", "jodhpur", "agra", "delhi",
    "varanasi", "rishikesh", "shimla", "darjeeling", "gangtok", "amritsar",
    "mumbai", "hyderabad", "bengaluru", "mysuru", "munnar", "alappuzha", "kochi",
]


def test_health_and_documentation(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200
    assert "/budget/calculate" in client.get("/openapi.json").json()["paths"]


def test_destination_search_and_prices(client):
    assert len(client.get("/destinations").json()) == 20
    destination = client.get("/destinations", params={"q": "  GOA "}).json()[0]
    assert destination["hotel_price_from"] == 2000
    assert destination["activity_price_from"] == 500
    assert destination["currency"] == "INR"
    assert "manali" in {item["id"] for item in client.get("/destinations", params={"q": "mountain"}).json()}
    assert client.get("/destinations", params={"q": "nonexistent"}).json() == []


def test_expanded_catalog_integrity(client):
    destinations = client.get("/destinations").json()
    hotels = client.get("/hotels").json()
    activities = client.get("/activities").json()
    destination_ids = {item["id"] for item in destinations}
    assert destination_ids == set(DESTINATION_IDS)
    assert len(destinations) == len(destination_ids) == 20
    assert len(hotels) == len({item["id"] for item in hotels}) == 40
    assert len(activities) == len({item["id"] for item in activities}) == 60
    for items, price_key in [(hotels, "price_per_night"), (activities, "price_per_person")]:
        assert all(item["destination_id"] in destination_ids for item in items)
        assert all(type(item[price_key]) is int and item[price_key] > 0 for item in items)
        assert all(item["currency"] == "INR" for item in items)


@pytest.mark.parametrize("destination_id", DESTINATION_IDS)
def test_every_destination_supports_search_filters_and_budget(client, destination_id):
    matches = client.get("/destinations", params={"q": destination_id.upper()}).json()
    assert destination_id in {item["id"] for item in matches}
    hotels = client.get("/hotels", params={"destination_id": destination_id}).json()
    activities = client.get("/activities", params={"destination_id": destination_id}).json()
    assert len(hotels) == 2
    assert len(activities) == 3
    assert all(item["destination_id"] == destination_id for item in [*hotels, *activities])
    if destination_id not in {"goa", "jaipur", "manali"}:
        assert all(item["available"] for item in [*hotels, *activities])
    hotel = next(item for item in hotels if item["available"])
    activity = next(item for item in activities if item["available"])
    response = client.post("/budget/calculate", json={
        "destination_id": destination_id, "hotel_id": hotel["id"],
        "activity_ids": [activity["id"]], "nights": 2, "rooms": 1,
        "travelers": 2, "food_total": 1000, "transport_total": 500, "misc_total": 200,
    })
    assert response.status_code == 200
    assert response.json()["total"] == hotel["price_per_night"] * 2 + activity["price_per_person"] * 2 + 1700
    assert response.json()["destination"] == matches[0]["name"]


def test_combined_hotel_filters(client):
    params = {"destination_id": "goa", "q": "BEACH", "max_price": 3000, "available_only": True}
    assert [item["id"] for item in client.get("/hotels", params=params).json()] == ["goa-beach"]
    params["max_price"] = 2999
    assert client.get("/hotels", params=params).json() == []
    assert len(client.get("/hotels", params={"destination_id": "goa"}).json()) == 2


def test_combined_activity_filters(client):
    params = {"destination_id": "goa", "q": "guided", "max_price": 600, "available_only": True}
    assert [item["id"] for item in client.get("/activities", params=params).json()] == ["goa-walk"]
    assert client.get("/activities", params={"max_price": 0}).json() == []
    assert client.get("/activities", params={"q": "nothing-like-this"}).json() == []


@pytest.mark.parametrize("path", ["/hotels", "/activities"])
def test_invalid_filters(client, path):
    assert client.get(path, params={"destination_id": "missing"}).status_code == 404
    assert client.get(path, params={"max_price": -1}).status_code == 422


@pytest.mark.parametrize("kind,item_id", [("hotels", "goa-beach"), ("activities", "goa-walk")])
def test_availability_changes_searches_and_budget(client, trip, kind, item_id):
    path = f"/{kind}/{item_id}/availability"
    assert client.patch(path, json={"available": False}).json()["available"] is False
    assert item_id not in [item["id"] for item in client.get(f"/{kind}", params={"available_only": True}).json()]
    assert client.post("/budget/calculate", json=trip).status_code == 409
    assert client.patch(path, json={"available": True}).json()["available"] is True
    assert client.post("/budget/calculate", json=trip).status_code == 200
    assert client.patch(path, json={"available": "false"}).status_code == 422


def test_reset_and_independent_app_stores(client):
    client.patch("/hotels/goa-beach/availability", json={"available": False})
    client.patch("/hotels/goa-garden/availability", json={"available": True})
    client.patch("/activities/goa-cruise/availability", json={"available": True})
    with TestClient(create_app()) as another_app:
        hotel = another_app.get("/hotels", params={"q": "Palm"}).json()[0]
        assert hotel["available"] is True
    assert client.post("/availability/reset").status_code == 200
    hotels = {item["id"]: item["available"] for item in client.get("/hotels").json()}
    activities = {item["id"]: item["available"] for item in client.get("/activities").json()}
    assert hotels["goa-beach"] is True
    assert hotels["goa-garden"] is False
    assert activities["goa-cruise"] is False


def test_budget_arithmetic_and_breakdown(client, trip):
    response = client.post("/budget/calculate", json=trip)
    assert response.status_code == 200
    result = response.json()
    assert result["hotel_total"] == 12000
    assert result["hotel"]["quantity"] == 4
    assert result["activities_total"] == 1500
    assert result["activities"][0]["quantity"] == 3
    assert result["total"] == 15200
    assert result["per_traveler"] == 5066.67
    assert result["currency"] == "INR"


def test_budget_with_no_activities_or_extras(client):
    response = client.post("/budget/calculate", json={"destination_id": "goa", "hotel_id": "goa-beach"})
    assert response.status_code == 200
    result = response.json()
    assert result["activities"] == []
    assert result["activities_total"] == 0
    assert result["total"] == result["per_traveler"] == 3000


def test_multiple_activities_are_charged_once_per_traveler(client, trip):
    trip["activity_ids"] = ["goa-walk", "goa-kayak"]
    result = client.post("/budget/calculate", json=trip).json()
    assert result["activities_total"] == (500 + 1200) * 3
    assert result["total"] == 18800


@pytest.mark.parametrize("field,value", [
    ("nights", 0), ("rooms", -1), ("travelers", 0), ("nights", 1.5),
    ("rooms", True), ("food_total", -1), ("transport_total", -1),
    ("misc_total", 0.5), ("activity_ids", ["goa-walk", "goa-walk"]),
    ("hotel_id", "jaipur-haveli"), ("activity_ids", ["manali-trek"]),
])
def test_budget_invalid_inputs(client, trip, field, value):
    trip[field] = value
    assert client.post("/budget/calculate", json=trip).status_code == 422


@pytest.mark.parametrize("field,value", [
    ("destination_id", "missing"), ("hotel_id", "missing"), ("activity_ids", ["missing"]),
])
def test_budget_unknown_ids(client, trip, field, value):
    trip[field] = value
    assert client.post("/budget/calculate", json=trip).status_code == 404


@pytest.mark.parametrize("path", ["/hotels/missing/availability", "/activities/missing/availability"])
def test_unknown_availability_item(client, path):
    assert client.patch(path, json={"available": True}).status_code == 404
