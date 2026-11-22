"""Run: uv run streamlit run streamlit_app.py --server.port 8503"""

import os

import streamlit as st

from frontend.api_client import APIClient, APIError
from frontend import views

st.set_page_config(page_title="Shopflow release lab", page_icon=":material/fact_check:", layout="wide")
st.title("Shopflow release lab")
st.write("Inspect release evidence, edit simulated outcomes, and evaluate deployment rules.")
st.caption("Fictional data for learning and future CrewAI tool testing. No agents, scans, or deployments run here.")

# Session state contains UI feedback only; the backend owns the shared evidence.
if "refresh_token" not in st.session_state:
    st.session_state["refresh_token"] = 0

with st.sidebar:
    st.header("Connection")
    base_url = st.text_input("API base URL", value=os.getenv("SHOPFLOW_API_URL", "http://127.0.0.1:8001"), key="api_url")
    st.button("Refresh evidence", key="refresh", icon=":material/refresh:")

api = APIClient(base_url)
try:
    api.get("/health")
    releases = api.get("/releases")
except APIError as error:
    st.error(str(error))
    st.info("Start the backend from the shopflow folder:")
    st.code("uv run uvicorn backend.main:app --reload --port 8001", language="powershell")
    st.stop()

if not releases:
    st.info("No release candidates are available.")
    st.stop()

by_id = {item["id"]: item for item in releases}
with st.sidebar:
    st.success("API connected")
    st.link_button("Open API documentation", api.base_url + "/docs", icon=":material/open_in_new:")
    release_id = st.selectbox("Release candidate", list(by_id), format_func=lambda value: f"{by_id[value]['version']} — {by_id[value]['initial_scenario'].replace('_', ' ')} seed", key="release_id")
    environment = st.segmented_control("Environment", ["staging", "production"], default="production", required=True, key="environment")
    section = st.selectbox("Section", ["Overview", "Tests", "Bugs", "Security", "Documentation", "Deployment"], key="section")
    st.divider()
    st.caption("Changes are shared across clients of this backend. Restart or reset restores seeded evidence.")
    reset = st.button("Reset selected release", key="reset_release", icon=":material/restart_alt:")

prefix = f"/releases/{release_id}"
if reset:
    try:
        api.post(prefix + "/reset")
    except APIError as error:
        st.error(str(error))
    else:
        views.updated("Selected release restored to its sample data.")

notice = st.session_state.pop("notice", None)
if notice:
    st.success(notice)
st.subheader(by_id[release_id]["title"])
st.caption(by_id[release_id]["description"])

# Only the selected screen loads its evidence; hidden sections do no work.
try:
    if section == "Overview":
        views.overview(api, prefix)
    elif section == "Tests":
        views.tests(api, prefix, release_id)
    elif section == "Bugs":
        views.bugs(api, prefix, release_id)
    elif section == "Security":
        views.security(api, prefix, release_id)
    elif section == "Documentation":
        views.documentation(api, prefix, release_id)
    elif section == "Deployment":
        views.deployment(api, prefix, str(environment), release_id)
except APIError as error:
    st.error(str(error))
