"""A small, stateless HTTP client that future agent tools can also wrap."""

import requests


class APIError(RuntimeError):
    """An API failure suitable for display in the learning UI."""


class APIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.strip().rstrip("/")

    def _request(self, method: str, path: str, *, params=None, payload=None):
        try:
            response = requests.request(
                method, self.base_url + path, params=params, json=payload, timeout=5,
            )
        except requests.RequestException:
            raise APIError("Cannot reach the Shopflow API. Start the backend and check the API base URL.") from None
        try:
            data = response.json()
        except ValueError:
            raise APIError("The server returned an unexpected response. Check the API base URL.") from None
        if not response.ok:
            detail = data.get("detail", "Request failed.") if isinstance(data, dict) else "Request failed."
            if isinstance(detail, list):
                detail = "; ".join(
                    f"{'.'.join(str(part) for part in issue.get('loc', []))}: {issue.get('msg', 'Invalid value')}"
                    for issue in detail
                )
            raise APIError(f"API {response.status_code}: {detail}")
        return data

    def get(self, path: str, **params):
        return self._request("GET", path, params={key: value for key, value in params.items() if value is not None})

    def patch(self, path: str, payload: dict):
        return self._request("PATCH", path, payload=payload)

    def post(self, path: str, payload: dict | None = None):
        return self._request("POST", path, payload=payload)
