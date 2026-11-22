import json
from unittest.mock import Mock

import pytest
import requests

from frontend.api_client import APIClient, APIError


def response(code, body):
    result = requests.Response()
    result.status_code = code
    result._content = json.dumps(body).encode()
    return result


def test_optional_filters_preserve_false_and_request_timeout(monkeypatch):
    request = Mock(return_value=response(200, []))
    monkeypatch.setattr(requests, "request", request)
    assert APIClient("http://localhost:8001/").get("/tests", status=None, critical=False) == []
    request.assert_called_once_with("GET", "http://localhost:8001/tests", params={"critical": False}, json=None, timeout=5)


def test_validation_errors_are_readable(monkeypatch):
    monkeypatch.setattr(requests, "request", Mock(return_value=response(422, {"detail": [{"loc": ["body", "status"], "msg": "Invalid status"}]})))
    with pytest.raises(APIError, match="body.status: Invalid status"):
        APIClient("http://localhost").patch("/tests/test", {"status": "bad"})


@pytest.mark.parametrize("error", [requests.ConnectionError(), requests.Timeout()])
def test_connection_errors(monkeypatch, error):
    monkeypatch.setattr(requests, "request", Mock(side_effect=error))
    with pytest.raises(APIError, match="Cannot reach"):
        APIClient("http://localhost").get("/health")


def test_non_json_response(monkeypatch):
    result = response(200, {})
    result._content = b"<html>Wrong service</html>"
    monkeypatch.setattr(requests, "request", Mock(return_value=result))
    with pytest.raises(APIError, match="unexpected response"):
        APIClient("http://localhost").get("/health")
