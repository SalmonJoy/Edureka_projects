"""The three Streamlit screens. Every search and calculation uses the API."""

import streamlit as st

from frontend.api_client import APIClient, APIError


def money(amount: int | float) -> str:
    return f"₹{amount:,.2f}"


def render_catalog(api: APIClient, destinations: list[dict]) -> None:
    st.subheader("Explore the travel catalog")
    st.caption("Fictional prices in INR. Hotel prices are per room/night; activity prices are per person.")
    destination_names = {item["id"]: item["name"] for item in destinations}

    with st.form("catalog_filters"):
        destination_query = st.text_input("Search destinations", placeholder="Try beach, Goa, or mountain")
        destination_id = st.selectbox(
            "Filter hotels and activities by destination", [None, *destination_names],
            format_func=lambda value: destination_names.get(value, "All destinations"),
        )
        left, right = st.columns(2)
        hotel_query = left.text_input("Search hotels")
        activity_query = right.text_input("Search activities")
        hotel_max = left.number_input("Maximum hotel price (INR/night; 0 = no limit)", min_value=0, value=0, step=500)
        activity_max = right.number_input("Maximum activity price (INR/person; 0 = no limit)", min_value=0, value=0, step=100)
        available_only = st.checkbox("Show only available hotels and activities")
        st.form_submit_button("Search catalog")

    try:
        matches = api.get("/destinations", q=destination_query)
        hotels = api.get("/hotels", destination_id=destination_id, q=hotel_query, max_price=hotel_max or None, available_only=available_only)
        activities = api.get("/activities", destination_id=destination_id, q=activity_query, max_price=activity_max or None, available_only=available_only)
    except APIError as error:
        st.error(str(error))
        return

    st.markdown("#### Destinations")
    st.caption("Starting prices include unavailable sample items. Use the availability filter below when planning.")
    if matches:
        st.dataframe([
            {"Destination": item["name"], "Description": item["description"],
             "Hotel from / room / night": money(item["hotel_price_from"]),
             "Activity from / person": money(item["activity_price_from"])}
            for item in matches
        ], hide_index=True, width="stretch")
    else:
        st.info("No destinations match your search.")

    for heading, items, price_key, price_label in [
        ("Hotels", hotels, "price_per_night", "Price / room / night"),
        ("Activities", activities, "price_per_person", "Price / person"),
    ]:
        st.markdown(f"#### {heading}")
        if not items:
            st.info(f"No {heading.lower()} match your filters.")
            continue
        st.dataframe([
            {"Name": item["name"], "Destination": destination_names[item["destination_id"]],
             "Description": item["description"], price_label: money(item[price_key]),
             "Status": "Available" if item["available"] else "Unavailable"}
            for item in items
        ], hide_index=True, width="stretch")


def render_availability(api: APIClient, destinations: list[dict], hotels: list[dict], activities: list[dict]) -> None:
    st.subheader("Simulate availability")
    st.caption("Changes apply to everyone using this backend. Reset or restart restores the sample states.")
    names = {item["id"]: item["name"] for item in destinations}
    kind = st.radio("Item type", ["Hotel", "Activity"], horizontal=True)
    items = hotels if kind == "Hotel" else activities
    if items:
        by_id = {item["id"]: item for item in items}
        item_id = st.selectbox(
            "Choose an item", list(by_id), key=f"availability_{kind}",
            format_func=lambda value: f"{by_id[value]['name']} — {names[by_id[value]['destination_id']]}",
        )
        item = by_id[item_id]
        st.write(f"Current status: **{'Available' if item['available'] else 'Unavailable'}**")
        if st.button("Make unavailable" if item["available"] else "Make available", key=f"toggle_{kind}"):
            path = "hotels" if kind == "Hotel" else "activities"
            try:
                api.patch(f"/{path}/{item_id}/availability", {"available": not item["available"]})
            except APIError as error:
                st.error(str(error))
            else:
                st.session_state.pop("budget_result", None)
                st.session_state["notice"] = f"Updated availability for {item['name']}."
                st.rerun()

    st.divider()
    if st.button("Reset all sample availability"):
        try:
            api.post("/availability/reset")
        except APIError as error:
            st.error(str(error))
        else:
            st.session_state.pop("budget_result", None)
            st.session_state["notice"] = "Sample availability restored (including initially unavailable items)."
            st.rerun()


def render_budget(api: APIClient, destinations: list[dict], hotels: list[dict], activities: list[dict]) -> None:
    st.subheader("Estimate your trip budget")
    st.caption("Plan one destination and one hotel. Each selected activity is attended once by every traveler.")
    if not destinations:
        st.info("No destinations are available.")
        return

    names = {item["id"]: item["name"] for item in destinations}
    destination_id = st.selectbox("Trip destination", list(names), format_func=names.get, key="budget_destination")
    if st.session_state.get("previous_destination") != destination_id:
        st.session_state.pop("budget_hotel", None)
        st.session_state.pop("budget_activities", None)
        st.session_state.pop("budget_result", None)
        st.session_state["previous_destination"] = destination_id

    hotel_options = {item["id"]: item for item in hotels if item["destination_id"] == destination_id and item["available"]}
    activity_options = {item["id"]: item for item in activities if item["destination_id"] == destination_id and item["available"]}

    # The simulator or another client may have made a previous selection unavailable.
    if st.session_state.get("budget_hotel") not in hotel_options:
        st.session_state.pop("budget_hotel", None)
    selected_ids = st.session_state.get("budget_activities", [])
    valid_ids = [item_id for item_id in selected_ids if item_id in activity_options]
    if valid_ids != selected_ids:
        st.session_state["budget_activities"] = valid_ids
        st.session_state.pop("budget_result", None)

    if not hotel_options:
        st.session_state.pop("budget_result", None)
        st.warning("No hotels are available here. Use the simulator to make a hotel available, or choose another destination.")
        return

    with st.form("budget_form"):
        hotel_id = st.selectbox(
            "Hotel", list(hotel_options), key="budget_hotel",
            format_func=lambda value: f"{hotel_options[value]['name']} — {money(hotel_options[value]['price_per_night'])} / room / night",
        )
        activity_ids = st.multiselect(
            "Activities", list(activity_options), key="budget_activities",
            format_func=lambda value: f"{activity_options[value]['name']} — {money(activity_options[value]['price_per_person'])} / person",
        )
        left, middle, right = st.columns(3)
        nights = left.number_input("Nights", min_value=1, value=2, step=1)
        rooms = middle.number_input("Rooms", min_value=1, value=1, step=1)
        travelers = right.number_input("Travelers", min_value=1, value=2, step=1)
        st.caption("Extras are totals for the entire group and trip. Enter whole rupees; no automatic taxes or fees are added.")
        food = left.number_input("Food total (INR)", min_value=0, value=0, step=100)
        transport = middle.number_input("Transport total (INR)", min_value=0, value=0, step=100)
        misc = right.number_input("Miscellaneous total (INR)", min_value=0, value=0, step=100)
        submitted = st.form_submit_button("Calculate budget")

    payload = {
        "destination_id": destination_id, "hotel_id": hotel_id, "activity_ids": activity_ids,
        "nights": nights, "rooms": rooms, "travelers": travelers,
        "food_total": food, "transport_total": transport, "misc_total": misc,
    }
    if submitted:
        st.session_state.pop("budget_result", None)
        try:
            result = api.post("/budget/calculate", payload)
        except APIError as error:
            st.error(str(error))
        else:
            st.session_state["budget_result"] = {"inputs": payload, "estimate": result}

    saved = st.session_state.get("budget_result")
    if not saved or saved["inputs"] != payload:
        return
    result = saved["estimate"]
    st.markdown("#### Budget breakdown")
    st.caption("This is an estimate from your last calculation, not a reservation. Recalculate to check current availability.")
    left, right = st.columns(2)
    left.metric("Total trip cost", money(result["total"]))
    right.metric("Per traveler (estimate)", money(result["per_traveler"]))
    rows = [{"Item": result["hotel"]["name"], "Calculation": f"{money(result['hotel']['unit_price'])} × {nights} nights × {rooms} rooms", "Cost (INR)": result["hotel_total"]}]
    rows.extend({"Item": line["name"], "Calculation": f"{money(line['unit_price'])} × {travelers} travelers", "Cost (INR)": line["total"]} for line in result["activities"])
    rows.extend({"Item": label, "Calculation": "Whole-trip group total", "Cost (INR)": result[key]} for label, key in [("Food", "food_total"), ("Transport", "transport_total"), ("Miscellaneous", "misc_total")])
    st.dataframe(rows, hide_index=True, width="stretch")
