"""Interactive CLI: uv run python main.py --destination jaipur"""

import argparse
import json

from pydantic import ValidationError

from agent.errors import ServiceError
from agent.models import TripRequest
from agent.runtime import create_runtime


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Step through a LangGraph itinerary simulation.")
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument("--destination", default="jaipur")
    parser.add_argument("--nights", type=int, default=2)
    parser.add_argument("--rooms", type=int, default=1)
    parser.add_argument("--travelers", type=int, default=2)
    parser.add_argument("--budget", type=int, default=20000)
    parser.add_argument("--food", type=int, default=0)
    parser.add_argument("--transport", type=int, default=0)
    parser.add_argument("--misc", type=int, default=0)
    parser.add_argument("--preferences", default="I enjoy local food, culture, and a relaxed pace.")
    parser.add_argument("--test-memory", action="store_true", help="Isolate this run from normal memories using is_test=true.")
    args = parser.parse_args(argv)
    runtime = None
    try:
        request = TripRequest(
            user_id=args.user_id, destination_id=args.destination, nights=args.nights,
            rooms=args.rooms, travelers=args.travelers, max_budget=args.budget,
            food_total=args.food, transport_total=args.transport, misc_total=args.misc,
            preferences=args.preferences,
        )
        runtime = create_runtime()
        view = runtime.start_trip(request, is_test=args.test_memory)
        printed_events = 0
        while True:
            print(f"\nStatus: {view['status']} | Revisions: {view['replan_count']}/3")
            for event in view["events"][printed_events:]:
                print(f"[{event['node']}] {event['message']}")
            printed_events = len(view["events"])
            if view.get("plan"):
                print(json.dumps(view["plan"], ensure_ascii=True, indent=2))
            if view.get("reflection"):
                print(f"Rubric: {view['reflection']['total']}/10")
            if view.get("budget"):
                print(f"Latest API estimate: INR {view['budget']['total']}")
            if view.get("error"):
                print(view["error"])
            if view["status"] != "paused":
                print("Qdrant memory: " + ("saved" if view["memory_saved"] else "not confirmed"))
                if view.get("memory_error"):
                    print(view["memory_error"])
                return 0 if view["status"] == "complete" else 1
            print("Next simulated check:", json.dumps(view["pending"][0]))
            choice = input("Change availability in the simulator if desired. Enter=continue, q=quit: ")
            if choice.strip().lower() == "q":
                print("Stopped. This process's paused checkpoint will be discarded; no final memory was saved.")
                return 0
            view = runtime.continue_trip(view["run_id"])
    except (ValueError, ValidationError, ServiceError) as error:
        print(f"Agent stopped: {error}")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nStopped. In-memory checkpoints do not survive process exit.")
        return 0
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
