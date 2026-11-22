"""All UI-to-API communication lives here; no business logic is duplicated."""

import requests


class APIError(Exception):
    """An API or connection failure that can be displayed to the learner."""


class APIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.strip().rstrip("/")

    def _request(self, method: str, path: str, *, params=None, payload=None):
        try:
            response = requests.request(
                method, f"{self.base_url}{path}",
                params=params, json=payload, timeout=5,
            )
        except requests.RequestException as error:
            raise APIError(
                f"Cannot reach the travel API at {self.base_url}. "
                "Check the API URL and start the FastAPI server."
            ) from error

        try:
            data = response.json()
        except ValueError as error:
            raise APIError("The API returned an unexpected response. Check the API URL.") from error

        if not response.ok:
            detail = data.get("detail", "The request failed.") if isinstance(data, dict) else "The request failed."
            if isinstance(detail, list):
                # FastAPI validation errors contain a location and a message.
                detail = "; ".join(
                    f"{'.'.join(str(part) for part in issue.get('loc', []))}: {issue.get('msg', 'Invalid value')}"
                    for issue in detail
                )
            raise APIError(f"API {response.status_code}: {detail}")
        return data

    def get(self, path: str, **params):
        # Omit optional filters rather than sending a literal 'None'.
        return self._request("GET", path, params={key: value for key, value in params.items() if value is not None})

    def patch(self, path: str, payload: dict):
        return self._request("PATCH", path, payload=payload)

    def post(self, path: str, payload: dict | None = None):
        return self._request("POST", path, payload=payload)
