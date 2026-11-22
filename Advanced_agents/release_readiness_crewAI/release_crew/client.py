"""Only GET evidence and the non-mutating readiness check are supported."""

from urllib.parse import quote

import httpx
from pydantic import ValidationError

from release_crew.errors import ReviewError
from release_crew.models import PolicyCheck, ReleaseInfo, ReviewRequest


class ShopflowClient:
    def __init__(self, base_url: str, http: httpx.Client | None = None):
        self.base_url = base_url.strip().rstrip("/")
        self.http = http if http is not None else httpx.Client(timeout=10, trust_env=False)
        self._owns_http = http is None

    def close(self):
        if self._owns_http:
            self.http.close()

    @staticmethod
    def prefix(release_id: str) -> str:
        return "/releases/" + quote(release_id, safe="")

    def _read(self, path: str, *, environment: str | None = None):
        try:
            if environment is None:
                response = self.http.get(self.base_url + path)
            else:
                response = self.http.post(self.base_url + path, json={"environment": environment})
        except (httpx.HTTPError, httpx.InvalidURL):
            raise ReviewError("Cannot reach Shopflow. Start the API on port 8001 or check its URL.") from None
        if response.status_code != 200:
            raise ReviewError(f"Shopflow rejected {path} (HTTP {response.status_code}). No release recommendation was published.")
        try:
            return response.json()
        except ValueError:
            raise ReviewError("Shopflow returned invalid JSON.") from None

    def list_releases(self) -> list[ReleaseInfo]:
        try:
            return [ReleaseInfo.model_validate(item) for item in self._read("/releases")]
        except (ValidationError, TypeError):
            raise ReviewError("Shopflow returned invalid release metadata.") from None

    def read(self, source: str):
        # Sources are created by our evidence module, never by an LLM or user prompt.
        return self._read(source)

    def check(self, request: ReviewRequest) -> PolicyCheck:
        try:
            result = PolicyCheck.model_validate(self._read(self.prefix(request.release_id) + "/deployment-check", environment=request.environment))
            if result.release_id != request.release_id or result.environment != request.environment:
                raise ValueError("Wrong review scope")
            return result
        except (ValidationError, ValueError):
            raise ReviewError("Shopflow returned invalid or mismatched deployment gates.") from None
