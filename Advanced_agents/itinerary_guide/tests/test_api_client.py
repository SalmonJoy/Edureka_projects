import json
from unittest.mock import Mock

import pytest
import requests

from frontend.api_client import APIClient, APIError


def make_response(status: int, body) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def test_client_omits_missing_filters_and_sets_timeout(monkeypatch):
    request = Mock(return_value=make_response(200, []))
    monkeypatch.setattr(requests, "request", request)
    assert APIClient("http://localhost:8000/").get("/hotels", destination_id=None, max_price=0) == []
    request.assert_called_once_with("GET", "http://localhost:8000/hotels", params={"max_price": 0}, json=None, timeout=5)


@pytest.mark.parametrize("error", [requests.ConnectionError(), requests.Timeout()])
def test_connection_errors_are_readable(monkeypatch, error):
    monkeypatch.setattr(requests, "request", Mock(side_effect=error))
    with pytest.raises(APIError, match="Cannot reach the travel API"):
        APIClient("http://localhost:8000").get("/health")


def test_validation_error_is_readable(monkeypatch):
    response = make_response(422, {"detail": [{"loc": ["body", "nights"], "msg": "Must be positive"}]})
    monkeypatch.setattr(requests, "request", Mock(return_value=response))
    with pytest.raises(APIError, match="body.nights: Must be positive"):
        APIClient("http://localhost:8000").post("/budget/calculate", {})


def test_non_json_response_is_readable(monkeypatch):
    response = make_response(200, {})
    response._content = b"<html>Wrong server</html>"
    monkeypatch.setattr(requests, "request", Mock(return_value=response))
    with pytest.raises(APIError, match="unexpected response"):
        APIClient("http://localhost:8000").get("/health")
