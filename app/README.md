Author: Kyzer Fox

# AVWX - Aviation Weather & Flight Category Radar

A lightweight, high-performance aviation weather web service built with Python, Flask, and Vanilla HTML/CSS/JS. Provides real-time decoded METAR surface observations, Terminal Aerodrome Forecasts (TAF), FAA flight categories (VFR, MVFR, IFR, LIFR), and an interactive station map.

Designed for direct deployment to **Google Cloud Run** using Google Cloud Buildpacks (no Dockerfile required).

---

## Architecture & File Structure

```text
.
├── main.py               # Flask app: routes only (/, /api/metar, /api/taf)
├── weather.py            # Data fetching, unit cleaning, category filling & 5-minute cache
├── static/
│   ├── index.html        # Single-page interface & avionics theme
│   ├── app.js            # Frontend logic: API fetching, UI rendering, Leaflet map
│   └── style.css         # Dark avionics theme & glassmorphism CSS
├── tests/
│   ├── test_weather.py   # Unit & route tests against saved sample JSON fixtures
│   ├── sample_metar.json # Saved upstream METAR sample data
│   └── sample_taf.json   # Saved upstream TAF sample data
├── requirements.txt      # Production & test dependencies
├── .gitignore
└── README.md
```

---

## Features & Implementation

### 1. Server-Side Fetching & Cleaning (`weather.py`)
All fetching and data processing happens on the server, never in the browser:
- **Upstream Data Source**: Fetches from the free [aviationweather.gov Data API](https://aviationweather.gov/api/data/metar) with a descriptive `User-Agent`.
- **Unit Conversions**:
  - Temperature & Dewpoint: Celsius to Fahrenheit (`°C` and `°F`), temp-dewpoint spread.
  - Altimeter: Millibars/hPa converted to inches of mercury (`inHg`).
  - Wind: Degrees converted to 16-point cardinal compass (`N`, `NNW`, `NW`, etc.), speeds in knots with MPH equivalents.
  - Visibility: Formats string fractions (`1/2`, `1 1/2`, `10+`) to numeric statute miles (`visib_sm`) and clean display text.
- **Flight Category Inference**:
  - Automatically identifies or fills missing flight categories according to FAA criteria:
    - **LIFR** (Low IFR): Ceiling `< 500 ft` or Visibility `< 1 SM`
    - **IFR**: Ceiling `500 - 999 ft` or Visibility `1 - 2.9 SM`
    - **MVFR** (Marginal VFR): Ceiling `1000 - 3000 ft` or Visibility `3 - 5 SM`
    - **VFR**: Ceiling `> 3000 ft` and Visibility `> 5 SM`
- **Stale Report Marking**: Compares observation timestamp (`obsTime`) with the current UTC clock. Reports older than 75 minutes are flagged with `is_stale: true` and an age indicator.
- **5-Minute Memory Cache**: In-memory thread-safe cache with a 300-second TTL prevents hitting aviationweather.gov rate limits and provides sub-millisecond response times for frequent queries.

### 2. Thin Routing Layer (`main.py`)
- `GET /` — Serves the frontend single-page application.
- `GET /api/metar?ids=KMSP,KJFK` — Returns cleaned METAR JSON for one or more airports.
- `GET /api/taf?ids=KMSP,KJFK` — Returns cleaned TAF forecast JSON.
- Returns `400 Bad Request` if `ids` is omitted, and `502 Bad Gateway` if upstream aviation weather service cannot be reached.

### 3. Frontend Experience (`static/`)
- **Dark Avionics Glassmorphism Theme**: Inspired by modern electronic flight bag (EFB) and cockpit displays.
- **Interactive Leaflet Map**: Renders airports with glowing color-coded pins based on flight category and clickable summary popups.
- **Preset Chips & Multi-Station Query**: Supports querying multiple ICAO codes simultaneously with fast preset buttons for major hubs.
- **Raw METAR & TAF Inspection**: Includes raw decoded text blocks with one-click copy buttons.

---

## Local Development & Setup

### Prerequisites
- Python 3.10+
- `pip` / `venv`

### Setup Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run Tests
The test suite runs against saved sample JSON fixtures and tests cleaning, unit conversions, category calculation, caching, and routing:
```bash
pytest -v tests/
```

### Start the Development Server
```bash
python3 main.py
```
Or with Gunicorn:
```bash
gunicorn --bind 0.0.0.0:8080 --workers 2 main:app
```
Visit `http://localhost:8080` in your browser.

---

## Deployment to Google Cloud Run

Deploy directly using Google Cloud Buildpacks without a Dockerfile. With `main.py` located at the root of the source directory, Cloud Buildpacks automatically detects Python, installs dependencies from `requirements.txt`, and boots Gunicorn on the port set by `$PORT`.

```bash
# 1. Authenticate with Google Cloud
gcloud auth login

# 2. Set your Google Cloud project
gcloud config set project YOUR_PROJECT_ID

# 3. Deploy from source
gcloud run deploy avwx \
  --source . \
  --region us-central1 \
  --allow-unauthenticated
```

---

## Future Scale Consideration
If query volume grows significantly or historic weather trend archiving is needed:
- Add **Cloud Scheduler** triggering a cron worker every 5 minutes.
- The worker fetches and stores cleaned JSON into a **Google Cloud Storage (GCS)** bucket or Firestore.
- The Flask web service reads directly from Cloud Storage, isolating upstream API limits entirely.
