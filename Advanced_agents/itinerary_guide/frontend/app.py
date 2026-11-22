"""Start from the project folder: uv run streamlit run frontend/app.py"""

import os
import sys
from pathlib import Path

import streamlit as st

# Streamlit runs this file as a script. Include the project root for package imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frontend.api_client import APIClient, APIError
from frontend.screens import render_availability, render_budget, render_catalog


def main() -> None:
    st.set_page_config(page_title="Itinerary Guide", page_icon="🧳", layout="wide")
    st.title("🧳 Itinerary Guide")
    st.write("Explore a fictional travel catalog, simulate availability, and estimate a trip budget.")

    with st.sidebar:
        st.header("API connection")
        base_url = st.text_input("API base URL", value=os.getenv("TRAVEL_API_URL", "http://127.0.0.1:8000"))
        st.button("Refresh data")
        st.caption("Dummy data · INR prices · No API keys needed")
    if st.session_state.get("previous_api_url") != base_url:
        for key in ("budget_result", "budget_hotel", "budget_activities", "previous_destination"):
            st.session_state.pop(key, None)
        st.session_state["previous_api_url"] = base_url

    api = APIClient(base_url)
    try:
        api.get("/health")
        destinations = api.get("/destinations")
        hotels = api.get("/hotels")
        activities = api.get("/activities")
    except APIError as error:
        st.error(str(error))
        st.info("Run the backend in a separate terminal from the itinerary_guide folder:")
        st.code("uv run uvicorn backend.main:app --reload", language="powershell")
        return

    st.sidebar.success("API connected")
    st.sidebar.link_button("Open API documentation", f"{base_url.strip().rstrip('/')}/docs")
    notice = st.session_state.pop("notice", None)
    if notice:
        st.success(notice)

    catalog_tab, availability_tab, budget_tab = st.tabs(["Travel Catalog", "Availability Simulator", "Budget Calculator"])
    with catalog_tab:
        render_catalog(api, destinations)
    with availability_tab:
        render_availability(api, destinations, hotels, activities)
    with budget_tab:
        render_budget(api, destinations, hotels, activities)


if __name__ == "__main__":
    main()
