import httpx
import pytest

from agent.errors import ServiceError, TravelToolError
from agent.models import ItineraryPlan, Step, TripRequest
from agent.tools import TravelTools
from conftest import FakeTools, sample_plan


def test_http_tools_call_catalog_and_budget_without_mutations():
    fake = FakeTools()
    calls = []

    def handler(request):
        import json

        calls.append((request.method, request.url.path))
        if request.url.path == "/destinations":
            return httpx.Response(200, json=[fake.data.destination.model_dump()])
        if request.url.path == "/hotels":
            assert request.url.params["destination_id"] == "jaipur"
            return httpx.Response(200, json=[item.model_dump() for item in fake.data.hotels.values()])
        if request.url.path == "/activities":
            return httpx.Response(200, json=[item.model_dump() for item in fake.data.activities.values()])
        payload = json.loads(request.content)
        assert payload["activity_ids"] == ["jaipur-food", "jaipur-fort"]
        assert "price" not in payload
        return httpx.Response(200, json=fake.quote(TripRequest(), ItineraryPlan.model_validate(sample_plan())))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        tools = TravelTools("http://travel", http)
        assert len(tools.catalog("jaipur").hotels) == 2
        assert tools.check_step(TripRequest(), Step(kind="hotel", item_id="jaipur-haveli")).available
        assert tools.quote(TripRequest(), ItineraryPlan.model_validate(sample_plan()))["total"] == 7800
    assert all(method == "GET" or path == "/budget/calculate" for method, path in calls)


@pytest.mark.parametrize("code", [404, 409, 422])
def test_expected_tool_rejections_keep_status(code):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(code, json={"detail": "Rejected"}))) as http:
        with pytest.raises(TravelToolError) as caught:
            TravelTools("http://travel", http).quote(TripRequest(), ItineraryPlan.model_validate(sample_plan()))
    assert caught.value.status_code == code


def test_transport_errors_are_readable():
    def offline(request):
        raise httpx.ConnectError("raw error", request=request)

    with httpx.Client(transport=httpx.MockTransport(offline)) as http:
        with pytest.raises(ServiceError, match="Travel API connection failed"):
            TravelTools("http://travel", http).destinations()


@pytest.mark.parametrize("body", ["not JSON", '{"bad": "shape"}'])
def test_malformed_api_data_is_rejected(body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))) as http:
        with pytest.raises(ServiceError):
            TravelTools("http://travel", http).destinations()


def test_server_failure_is_fatal():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as http:
        with pytest.raises(ServiceError, match="server error"):
            TravelTools("http://travel", http).destinations()
