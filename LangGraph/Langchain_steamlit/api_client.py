"""Small HTTP-only client; no database credentials, LangGraph, or Ollama imports."""

import copy
import os
from urllib.parse import quote, urlsplit

import httpx


DEFAULT_API_URL = os.getenv("LANGGRAPH_API_URL", "http://127.0.0.1:8003")
GET_TIMEOUT = 10
POST_TIMEOUT = 600


class ApiClientError(RuntimeError):
    def __init__(self, message, *, status_code=None, thread_id=None, conversation_id=None, uncertain=False):
        super().__init__(message)
        self.status_code = status_code
        self.thread_id = thread_id
        self.conversation_id = conversation_id
        self.uncertain = uncertain


def normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError:
        raise ValueError("Use a valid API port number.") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an HTTP(S) API URL without credentials, query parameters, or a fragment.")
    return value


def validate_workflow(value) -> dict:
    """Check the fields rendered by the UI rather than importing backend models."""
    def text(item):
        return isinstance(item, str) and bool(item.strip())

    if not isinstance(value, dict) or not all(text(value.get(key)) for key in ("thread_id", "conversation_id", "status")):
        raise ValueError("Missing workflow identity or status")
    if value.get("message") is not None and not isinstance(value["message"], str):
        raise ValueError("Invalid workflow message")
    warnings = value.get("warnings", [])
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise ValueError("Invalid warnings")
    pending = value.get("interrupt")
    if pending is not None:
        if not isinstance(pending, dict) or pending.get("kind") not in ("clarification", "confirmation"):
            raise ValueError("Invalid interrupt")
        if not all(text(pending.get(key)) for key in ("prompt_id", "question")) or not isinstance(pending.get("details"), dict):
            raise ValueError("Incomplete interrupt")
        if pending["kind"] == "confirmation":
            details = pending["details"]
            if not all(isinstance(details.get(key), dict) for key in ("employee", "leave", "eligibility")):
                raise ValueError("Incomplete review details")
            if not all(text(details["leave"].get(key)) for key in ("start_date", "end_date", "reason", "leave_type")):
                raise ValueError("Incomplete leave details")
            if not all(type(details.get(key)) is int for key in ("requested_days", "leave_balance")):
                raise ValueError("Invalid day counts")
            reasons = details["eligibility"].get("reasons")
            if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
                raise ValueError("Invalid policy reasons")
    application = value.get("application")
    if application is not None:
        if not isinstance(application, dict) or not text(application.get("application_id")):
            raise ValueError("Invalid application receipt")
        if not isinstance(application.get("leave"), dict) or not all(text(application["leave"].get(key)) for key in ("start_date", "end_date")):
            raise ValueError("Incomplete receipt dates")
    result = copy.deepcopy(value)
    for key in ("message", "interrupt", "application"):
        result.setdefault(key, None)
    result.setdefault("warnings", [])
    return result


class WorkflowApiClient:
    def __init__(self, base_url=DEFAULT_API_URL, *, client=None):
        self.base_url = normalize_url(base_url)
        self.client = client

    def _request(self, method, path, *, body=None, thread_id=None):
        timeout = GET_TIMEOUT if method == "GET" else POST_TIMEOUT
        try:
            if self.client is not None:
                response = self.client.request(method, self.base_url + path, json=body, timeout=timeout)
            else:
                # HTTPX does not retry by default. Never retry a POST automatically.
                with httpx.Client(trust_env=False) as client:
                    response = client.request(method, self.base_url + path, json=body, timeout=timeout)
        except httpx.RequestError as error:
            message = "The API did not return a result. Check that the FastAPI server is running."
            if isinstance(error, httpx.TimeoutException):
                message = "The API request timed out."
            if method != "GET":
                message += " The operation outcome may be unknown. Check saved status before retrying; no automatic retry was made."
            raise ApiClientError(message, thread_id=thread_id, uncertain=method != "GET") from None
        try:
            data = response.json()
        except ValueError:
            raise ApiClientError(
                "The API returned an unreadable response. Check the API URL and saved workflow status.",
                status_code=response.status_code, thread_id=thread_id, uncertain=method != "GET",
            ) from None
        if not response.is_success:
            detail = data.get("detail") if isinstance(data, dict) else None
            if isinstance(detail, list):
                detail = "; ".join(str(item.get("msg", "Invalid input")) for item in detail if isinstance(item, dict))
            if not isinstance(detail, str) or not detail.strip():
                detail = f"The API returned HTTP {response.status_code}."
            raise ApiClientError(
                detail, status_code=response.status_code,
                thread_id=data.get("thread_id", thread_id) if isinstance(data, dict) else thread_id,
                conversation_id=data.get("conversation_id") if isinstance(data, dict) else None,
                uncertain=method != "GET" and response.status_code >= 500,
            )
        try:
            result = validate_workflow(data)
            if thread_id is not None and result["thread_id"] != thread_id:
                raise ValueError("Response belongs to a different workflow")
            return result
        except ValueError:
            raise ApiClientError(
                "The API response is missing valid workflow details. Check saved status before retrying.",
                thread_id=thread_id, uncertain=method != "GET",
            ) from None

    @staticmethod
    def _thread(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Enter a workflow ID.")
        return quote(value.strip(), safe="")

    def start(self, request, conversation_id):
        if not isinstance(request, str) or not request.strip():
            raise ValueError("Enter a nonblank leave request.")
        return self._request("POST", "/workflows", body={"request": request.strip(), "conversation_id": conversation_id})

    def get(self, thread_id):
        return self._request("GET", f"/workflows/{self._thread(thread_id)}", thread_id=thread_id)

    def resume(self, thread_id, *, prompt_id=None, answer=None):
        body = {} if prompt_id is None else {"prompt_id": prompt_id, "answer": answer}
        return self._request("POST", f"/workflows/{self._thread(thread_id)}/resume", body=body, thread_id=thread_id)

    def cancel(self, thread_id):
        return self._request("POST", f"/workflows/{self._thread(thread_id)}/cancel", body=None, thread_id=thread_id)
