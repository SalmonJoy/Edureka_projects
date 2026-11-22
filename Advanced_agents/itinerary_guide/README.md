# Itinerary Guide

A small learning project with a **FastAPI backend** and a **Streamlit UI**. It uses fictional destinations, hotels, activities, and prices in Indian rupees (INR). Everything runs locally without API keys, Qdrant, OpenRouter, or a database.

## 1. Install and run

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required. Open PowerShell:

```powershell
cd 'C:\Salmon\Edureka\AI agents\Advanced_agents\itinerary_guide'
uv sync
```

In the first terminal, start the API:

```powershell
uv run uvicorn backend.main:app --reload
```

In a second terminal, change to the same folder and start the UI:

```powershell
uv run streamlit run frontend/app.py
```

- UI: <http://localhost:8501>
- Interactive API documentation (Swagger): <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health>

The UI sidebar lets you change the API URL. You can also set `$env:TRAVEL_API_URL = 'http://127.0.0.1:8000'` before starting Streamlit. No `.env` file is loaded by this project.

## 2. Try the three features

### Travel Catalog

Search **20 Indian destinations**, with **two hotels and three activities per destination**: **40 hotels and 60 activities** in total.

| Destination | API ID | Destination | API ID |
|---|---|---|---|
| Goa | `goa` | Jaipur | `jaipur` |
| Manali | `manali` | Udaipur | `udaipur` |
| Jodhpur | `jodhpur` | Agra | `agra` |
| Delhi | `delhi` | Varanasi | `varanasi` |
| Rishikesh | `rishikesh` | Shimla | `shimla` |
| Darjeeling | `darjeeling` | Gangtok | `gangtok` |
| Amritsar | `amritsar` | Mumbai | `mumbai` |
| Hyderabad | `hyderabad` | Bengaluru | `bengaluru` |
| Mysuru | `mysuru` | Munnar | `munnar` |
| Alappuzha | `alappuzha` | Kochi | `kochi` |

Filter hotels and activities by destination, name/description, maximum price, and availability. Searches ignore letter case and return an empty list when nothing matches. A UI price limit of **0** means no limit; the API accepts `max_price=0` as a literal zero-price ceiling.

Both this UI and the sibling itinerary agent obtain destinations from the API automatically. After changing the seed catalog, restart the backend (or let `--reload` reload it), then refresh the UIs. A backend reload restores seeded availability. All 17 added destinations start with every hotel and activity available; the original Goa, Jaipur, and Manali records retain their IDs, prices, and availability states.

Hotels cost **per room per night**. Activities cost **per person**, once per selected activity. Destination starting prices include all catalog items, including unavailable ones. All prices are fictional whole rupees.

### Availability Simulator

Choose a hotel or activity, then make it available or unavailable. Try making Goa's Palm Beach Stay unavailable and checking the budget hotel list. The reset button restores the original seed states: Coastal Garden Inn and Sunset Cruise start unavailable.

Availability is a single boolean, not date-based inventory. Changes are shared by all clients connected to one backend process. They survive UI reruns but disappear when the backend restarts or reloads. Run one API worker for this demo.

### Budget Calculator

Choose one destination, one available hotel, and zero or more available activities. Enter nights, rooms, travelers, and extras for the **whole group and trip**.

```text
Hotel cost    = price per room/night × nights × rooms
Activity cost = sum of selected per-person prices × travelers
Total         = hotel cost + activity cost + food + transport + miscellaneous
Per traveler  = total ÷ travelers (rounded to two decimal places)
```

Example: Palm Beach Stay at ₹3,000 × 2 nights × 2 rooms, Coastal Walking Tour at ₹500 × 3 travelers, and ₹1,700 in extras gives **₹15,200**. Per traveler is approximately ₹5,066.67.

Rooms are entered manually; there is no occupancy calculation. Each traveler attends every selected activity once. No taxes, fees, flights, or food costs are added automatically. Estimates do not book items or change availability. Recalculate to check current availability.

## 3. Understand the modules

```text
itinerary_guide/
├── backend/
│   ├── main.py             # Creates the FastAPI app and includes routers
│   ├── models.py           # Request/response models and input validation
│   ├── data.py             # Fictional seed catalog
│   ├── store.py            # In-memory storage and item lookups
│   ├── services.py         # Searches and budget formulas
│   └── routers/
│       ├── catalog.py      # Destination, hotel, and activity searches
│       ├── availability.py # Availability updates and reset
│       └── budget.py       # Budget calculation endpoint
├── frontend/
│   ├── app.py              # Streamlit entry point and connection settings
│   ├── api_client.py       # HTTP requests, timeouts, and readable errors
│   └── screens.py          # The three UI tabs
├── tests/                  # API, HTTP-client, and Streamlit tests
├── pyproject.toml          # Runtime and development dependencies
└── uv.lock                 # Resolved versions for reproducible installation
```

Request flow: **Streamlit widget → HTTP client → FastAPI route → service/store → JSON response → UI**. The frontend never imports backend data or repeats budget formulas.

`APIRouter` groups related endpoints. Pydantic validates request bodies and documents responses. `Depends(get_store)` supplies the running app's store to a route. `create_app()` creates fresh state for isolated tests. Streamlit reruns the UI on interactions; forms batch edits until submission. Budget results use session state, but catalog data is fetched again rather than cached.

## 4. Use the API directly

| Method | Path | Input / result |
|---|---|---|
| GET | `/health` | Returns `{"status": "ok"}` |
| GET | `/destinations` | Optional `q`; includes starting INR prices |
| GET | `/hotels` | Optional `destination_id`, `q`, `max_price`, `available_only` |
| GET | `/activities` | Same filters; `max_price` is per person |
| PATCH | `/hotels/{id}/availability` | Body: `{"available": false}` |
| PATCH | `/activities/{id}/availability` | Body: `{"available": true}` |
| POST | `/availability/reset` | No body; restores seeded availability |
| POST | `/budget/calculate` | Body below; returns an itemized estimate |

All filters are combined. Catalog searches include unavailable items unless `available_only=true`. An unknown destination filter returns `404`; a valid destination with no matches returns `[]`.

Try this PowerShell example while the backend is running:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/hotels?destination_id=goa&available_only=true'

$trip = @{
    destination_id = 'goa'
    hotel_id = 'goa-beach'
    activity_ids = @('goa-walk')
    nights = 2
    rooms = 2
    travelers = 3
    food_total = 1000
    transport_total = 500
    misc_total = 200
} | ConvertTo-Json

Invoke-RestMethod 'http://127.0.0.1:8000/budget/calculate' -Method Post -ContentType 'application/json' -Body $trip
```

Budget responses contain hotel and activity line items (`unit_price`, `quantity`, `total`), subtotals, extras, `total`, `per_traveler`, and `currency`. Hotel quantity is room-nights; activity quantity is travelers. The backend looks up current prices rather than accepting prices from the UI.

- `404`: an item or destination ID does not exist.
- `422`: invalid input, duplicate activities, or selections belonging to different destinations. Counts must be positive integers; extras must be nonnegative integers; availability must be a JSON boolean.
- `409`: a selected hotel or activity is currently unavailable, including when another client changes it before calculation.

This is a local teaching demo: there is no authentication, real booking, persistent storage, or external travel service.

## 5. Run checks

```powershell
uv run pytest
```

Tests use fresh stores, FastAPI's test client, and Streamlit's AppTest. They do not need running servers or API keys. To test manually, search the catalog, toggle and reset both item types, calculate the example above, switch destinations, and stop the API to see the UI's connection error message.
