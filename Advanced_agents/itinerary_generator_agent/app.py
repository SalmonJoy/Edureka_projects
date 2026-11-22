"""Streamlit UI: uv run streamlit run app.py --server.port 8502"""

import streamlit as st
from pydantic import ValidationError

from agent.config import load_settings
from agent.errors import ServiceError
from agent.models import TripRequest
from agent.runtime import create_runtime


def show_plan(plan: dict, catalog: dict) -> None:
    hotel = catalog.get("hotels", {}).get(plan["hotel_id"], {})
    st.write(f"**Hotel:** {hotel.get('name', plan['hotel_id'])}")
    st.caption(plan["rationale"])
    for day in sorted(plan["days"], key=lambda item: item["day"]):
        st.markdown(f"#### Day {day['day']}")
        for item_id in day["activity_ids"]:
            item = catalog.get("activities", {}).get(item_id, {})
            st.write(f"• {item.get('name', item_id)}")
        if day["free_time"]:
            st.write(f"Free time: {day['free_time']}")


def show_run(view: dict) -> None:
    st.subheader(f"Run status: {view['status']}")
    st.caption(f"Run {view['run_id']} · {view['replan_count']}/3 revisions · All checks are simulated; no bookings are made.")
    if view.get("error"):
        st.error(view["error"])
    if view["status"] == "complete":
        st.success("The itinerary passed final validation and all simulated checks.")
    if view["status"] in {"complete", "incomplete", "error"}:
        if view["memory_saved"]:
            st.success("Preferences and experience were saved to Qdrant.")
        else:
            st.warning("Memory persistence was not confirmed. " + (view.get("memory_error") or ""))

    itinerary, execution, reflection, memory = st.tabs(["Itinerary", "Execution trace", "Reflection", "Qdrant memory"])
    with itinerary:
        if view.get("plan"):
            show_plan(view["plan"], view["catalog"])
        else:
            st.info("No feasible itinerary has been produced.")
        if view.get("budget"):
            quote = view["budget"]
            st.markdown("#### Latest API budget quote")
            st.caption("A quote is an estimate at check time. Final availability is rechecked before completion.")
            left, middle, right = st.columns(3)
            left.metric("Whole-trip total", f"₹{quote['total']:,.2f}")
            middle.metric("Budget limit", f"₹{view['request']['max_budget']:,.2f}")
            right.metric("Per traveler", f"₹{quote['per_traveler']:,.2f}")
            st.dataframe([
                {"Category": label, "Cost (INR)": quote[key]}
                for label, key in [("Hotel", "hotel_total"), ("Activities", "activities_total"), ("Food", "food_total"), ("Transport", "transport_total"), ("Miscellaneous", "misc_total")]
            ], hide_index=True, width="stretch")
        if view.get("initial_plan"):
            with st.expander("Initial structured plan"):
                st.json(view["initial_plan"])
        with st.expander("Inputs frozen for this run"):
            st.json(view["request"])

    with execution:
        st.markdown("#### Pending checks")
        if view["pending"]:
            st.dataframe(view["pending"], hide_index=True, width="stretch")
        else:
            st.write("No pending checks.")
        st.markdown("#### Completed checks")
        if view["completed"]:
            st.dataframe([
                {"Kind": result["step"]["kind"], "Item": result["step"]["item_id"], "Day": result["step"]["day"], "Observation": result["observation"]}
                for result in view["completed"]
            ], hide_index=True, width="stretch")
        st.markdown("#### Node-by-node trace")
        st.caption("These are node results and decision summaries, not the model's private reasoning.")
        st.dataframe([{"#": index, **event} for index, event in enumerate(view["events"], start=1)], hide_index=True, width="stretch")
        with st.expander("All step observations, including unexpected results"):
            st.json(view["observations"])

    with reflection:
        result = view.get("reflection")
        if result:
            st.metric("Latest rubric score", f"{result['total']}/10")
            st.caption("Latest evaluated plan: " + ("passed" if result["passed"] else "needs revision") + ". The run status above also includes subsequent execution/service outcomes.")
            st.dataframe([
                {"Criterion": name.title(), "Score / 2": score["score"], "Reason": score["reason"]}
                for name, score in result["criteria"].items()
            ], hide_index=True, width="stretch")
            for issue in result["issues"]:
                st.write(f"• {issue}")
            with st.expander("Reflection history"):
                st.json(view["evaluations"])
        else:
            st.info("Reflection has not completed.")

    with memory:
        st.write("Semantic matches from **Edureka**, filtered to this application's current user.")
        st.caption("Current instructions override recalled preferences. Historical prices and failures are not current catalog facts.")
        if not view["memories"]:
            st.info("No past memories were retrieved for this user.")
        for record in view["memories"]:
            with st.expander(f"{record['type']} · similarity {record['score']:.3f} · {record['created_at']}"):
                st.text(record["text"])
        if view.get("memory_error"):
            st.warning(view["memory_error"])


def main() -> None:
    st.set_page_config(page_title="LangGraph Itinerary Agent", page_icon="🧭", layout="wide")
    st.title("🧭 LangGraph Itinerary Agent")
    st.write("Watch a planner, executor, replanner, and reflection evaluator work together.")
    with st.sidebar:
        if st.button("Reload configuration"):
            old = st.session_state.pop("agent_runtime", None)
            if old:
                old.close()
            st.session_state.pop("agent_settings", None)
            st.session_state.pop("run_view", None)
            st.rerun()
        st.caption("Reloading discards the active in-memory run. Saved Qdrant memories remain.")
    try:
        if "agent_runtime" not in st.session_state:
            settings = load_settings()
            st.session_state["agent_runtime"] = create_runtime(settings)
            st.session_state["agent_settings"] = settings
        runtime = st.session_state["agent_runtime"]
        settings = st.session_state["agent_settings"]
        destinations = runtime.services.tools.destinations()
    except (ValueError, ServiceError) as error:
        st.error(str(error))
        st.info("Start the itinerary_guide API and local Ollama, check the shared .env, then reload configuration.")
        return

    with st.sidebar:
        st.caption(f"LLM: {settings.model}\n\nEmbeddings: {settings.embedding_model}\n\nMemory collection: {settings.collection}")
        st.link_button("Open availability simulator", "http://localhost:8501")
        st.link_button("Travel API docs", settings.travel_url.rstrip("/") + "/docs")

    names = {item.id: item.name for item in destinations}
    if not names:
        st.error("The travel API returned no destinations.")
        return
    with st.expander("Trip inputs — submit to start a new run", expanded="run_view" not in st.session_state):
        with st.form("new_trip"):
            left, middle, right = st.columns(3)
            user_id = left.text_input("Memory user ID", value="demo-user")
            destination_id = middle.selectbox("Destination", list(names), index=list(names).index("jaipur") if "jaipur" in names else 0, format_func=names.get)
            max_budget = right.number_input("Whole-trip budget (INR)", min_value=1, value=20000, step=500)
            nights = left.number_input("Nights", min_value=1, max_value=14, value=2, step=1)
            rooms = middle.number_input("Rooms", min_value=1, max_value=30, value=1, step=1)
            travelers = right.number_input("Travelers", min_value=1, max_value=30, value=2, step=1)
            st.caption("Extras are whole-trip totals for the entire group. Rooms are entered manually.")
            food = left.number_input("Food total (INR)", min_value=0, value=0, step=100)
            transport = middle.number_input("Transport total (INR)", min_value=0, value=0, step=100)
            misc = right.number_input("Miscellaneous total (INR)", min_value=0, value=0, step=100)
            preferences = st.text_area("Preferences", value="I enjoy local food, culture, and a relaxed pace.")
            submitted = st.form_submit_button("Generate initial plan")
    if submitted:
        try:
            request = TripRequest(
                user_id=user_id, destination_id=destination_id, nights=nights, rooms=rooms,
                travelers=travelers, max_budget=max_budget, food_total=food,
                transport_total=transport, misc_total=misc, preferences=preferences,
            )
            with st.spinner("Retrieving memories, planning, and evaluating…"):
                st.session_state["run_view"] = runtime.start_trip(request)
        except (ValidationError, ServiceError, ValueError) as error:
            st.error(str(error))

    view = st.session_state.get("run_view")
    if view is None:
        st.info("Start a trip to inspect the initial plan and pause before the first simulated check.")
        return
    if view["status"] == "paused":
        step = view["pending"][0]
        st.info(f"Paused before {step['kind']}: {step['item_id']}. You can change availability in the simulator before continuing.")
        if st.button("Continue next step", type="primary"):
            with st.spinner("Checking the item and revising the plan if needed…"):
                st.session_state["run_view"] = runtime.continue_trip(view["run_id"])
            st.rerun()
    show_run(st.session_state["run_view"])


if __name__ == "__main__":
    main()
