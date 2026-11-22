"""Read-only travel checks and budget quotes over HTTP. No backend imports."""

import httpx
from pydantic import ValidationError

from agent.errors import ServiceError, TravelToolError
from agent.models import Activity, BudgetQuote, Catalog, Destination, Hotel, ItineraryPlan, Step, TripRequest


class TravelTools:
    def __init__(self, base_url: str, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.http = http if http is not None else httpx.Client(timeout=10, trust_env=False)
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self.http.request(method, self.base_url + path, **kwargs)
        except httpx.HTTPError:
            raise ServiceError("Travel API connection failed. Start itinerary_guide and check TRAVEL_API_URL.") from None
        if response.status_code >= 500:
            raise ServiceError("Travel API reported a server error. No simulated item was completed.")
        if response.status_code >= 400:
            raise TravelToolError(f"Travel API rejected {path} (HTTP {response.status_code}).", response.status_code)
        try:
            return response.json()
        except ValueError:
            raise ServiceError("Travel API returned invalid JSON.") from None

    def destinations(self) -> list[Destination]:
        try:
            return [Destination.model_validate(item) for item in self._request("GET", "/destinations")]
        except (ValidationError, TypeError):
            raise ServiceError("Travel API returned invalid destination data.") from None

    def catalog(self, destination_id: str) -> Catalog:
        destination = next((item for item in self.destinations() if item.id == destination_id), None)
        if destination is None:
            raise ServiceError("Unknown destination. Choose a destination provided by the travel API.")
        try:
            hotels = [Hotel.model_validate(item) for item in self._request("GET", "/hotels", params={"destination_id": destination_id})]
            activities = [Activity.model_validate(item) for item in self._request("GET", "/activities", params={"destination_id": destination_id})]
            if any(item.destination_id != destination_id for item in [*hotels, *activities]):
                raise ValueError("Wrong destination")
            return Catalog(destination=destination, hotels={item.id: item for item in hotels}, activities={item.id: item for item in activities})
        except (ValidationError, TypeError, ValueError):
            raise ServiceError("Travel API returned invalid catalog data.") from None

    def check_step(self, request: TripRequest, step: Step) -> Hotel | Activity | None:
        catalog = self.catalog(request.destination_id)
        items = catalog.hotels if step.kind == "hotel" else catalog.activities
        return items.get(step.item_id)

    def quote(self, request: TripRequest, plan: ItineraryPlan) -> dict:
        payload = {
            "destination_id": request.destination_id, "hotel_id": plan.hotel_id,
            "activity_ids": [item_id for day in plan.days for item_id in day.activity_ids],
            "nights": request.nights, "rooms": request.rooms, "travelers": request.travelers,
            "food_total": request.food_total, "transport_total": request.transport_total,
            "misc_total": request.misc_total,
        }
        try:
            quote = BudgetQuote.model_validate(self._request("POST", "/budget/calculate", json=payload))
            # Check correspondence; all arithmetic still comes from the API.
            if quote.hotel.id != plan.hotel_id or sorted(line.id for line in quote.activities) != sorted(payload["activity_ids"]):
                raise ValueError("Quote selections differ")
            return quote.model_dump()
        except (ValidationError, ValueError):
            raise ServiceError("Travel API returned an invalid or mismatched budget quote.") from None
