"""Explicit live check: real LLM calls and clearly tagged test memories."""

import argparse
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

import httpx

from agent.config import load_settings
from agent.memory import APP_ID
from agent.models import TripRequest
from agent.runtime import create_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--travel-url", help="Override the travel API URL for an isolated test server.")
    parser.add_argument("--simulate-hotel-failure", action="store_true", help="Temporarily mark the selected hotel unavailable, then restore it.")
    args = parser.parse_args()
    settings = load_settings()
    if args.travel_url:
        settings = replace(settings, travel_url=args.travel_url)
    runtime = create_runtime(settings)
    restore_hotel = None
    request = TripRequest(user_id="itinerary-agent-live-check", preferences="I enjoy local food, cultural experiences, and a relaxed pace.")
    try:
        view = runtime.start_trip(request, is_test=True)
        print(f"Initial status: {view['status']}", flush=True)
        if view["status"] != "paused":
            raise RuntimeError(view.get("error") or "The graph did not reach its first pause.")

        if args.simulate_hotel_failure:
            hotel_id = view["plan"]["hotel_id"]
            alternatives = [item for key, item in view["catalog"]["hotels"].items() if key != hotel_id and item["available"]]
            if not alternatives:
                raise RuntimeError("This check needs another available hotel. Use a fresh Jaipur sample catalog.")
            # This flag is explicit because it briefly changes simulator state.
            restore_hotel = hotel_id
            response = httpx.patch(settings.travel_url.rstrip("/") + f"/hotels/{hotel_id}/availability", json={"available": False}, timeout=10)
            response.raise_for_status()
            view = runtime.continue_trip(view["run_id"])
            if view["status"] != "paused" or view["plan"]["hotel_id"] == hotel_id or view["replan_count"] < 1:
                raise RuntimeError(view.get("error") or "Hotel failure did not produce a valid replacement.")
            print("Unexpected hotel result: replanned to an available alternative.", flush=True)

        for _ in range(10):
            if view["status"] != "paused":
                break
            view = runtime.continue_trip(view["run_id"])
            print(f"Status: {view['status']}; completed checks: {len(view['completed'])}", flush=True)
        if view["status"] != "complete" or not view["memory_saved"]:
            raise RuntimeError(view.get("error") or view.get("memory_error") or "Run did not finish successfully.")

        expected = {
            str(uuid5(NAMESPACE_URL, f"{APP_ID}/{request.user_id}/{view['run_id']}/{kind}"))
            for kind in ("preference", "experience")
        }
        stored = runtime.services.memory.client.retrieve(
            settings.collection, ids=list(expected), with_payload=True,
        )
        if {str(point.id) for point in stored} != expected:
            raise RuntimeError("The new verification records were not persisted.")
        # Top-five semantic matches need not contain every new point after many runs.
        recalled = runtime.services.memory.retrieve(request.model_dump(), is_test=True)
        if not recalled:
            raise RuntimeError("Semantic retrieval returned no saved memories.")
        # A paraphrased query exercises real semantic retrieval, not exact text matching.
        paraphrase = request.model_copy(update={"preferences": "Calm cultural breaks and regional cuisine"})
        similar = runtime.services.memory.retrieve(paraphrase.model_dump(), is_test=True)
        if not any(record["type"] == "preference" for record in similar):
            raise RuntimeError("Paraphrased semantic retrieval returned no preference memory.")
        print(f"Final rubric: {view['reflection']['total']}/10; API total: INR {view['budget']['total']}", flush=True)
        print(f"Qdrant persistence and semantic retrieval: passed; run_id={view['run_id']}", flush=True)
        print("Verification records remain in Edureka with is_test=true and user_id=itinerary-agent-live-check.", flush=True)
    finally:
        if restore_hotel:
            response = httpx.patch(settings.travel_url.rstrip("/") + f"/hotels/{restore_hotel}/availability", json={"available": True}, timeout=10)
            response.raise_for_status()
        runtime.close()


if __name__ == "__main__":
    main()
