"""
test_weather.py - Unit and integration tests for weather data cleaning,
caching, and API routes.
"""

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from main import app
import weather


@pytest.fixture
def sample_metar_data():
    """Load sample METAR JSON saved from aviationweather.gov."""
    fixture_path = os.path.join(os.path.dirname(__file__), "sample_metar.json")
    with open(fixture_path, "r") as f:
        return json.load(f)


@pytest.fixture
def sample_taf_data():
    """Load sample TAF JSON saved from aviationweather.gov."""
    fixture_path = os.path.join(os.path.dirname(__file__), "sample_taf.json")
    with open(fixture_path, "r") as f:
        return json.load(f)


@pytest.fixture
def client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_clean_sample_metar(sample_metar_data):
    """Test cleaning against real saved sample METAR data."""
    assert len(sample_metar_data) > 0
    raw_sample = sample_metar_data[0]
    cleaned = weather.clean_metar_record(raw_sample)

    assert "icao" in cleaned
    assert "temp_c" in cleaned
    assert "temp_f" in cleaned
    assert "dewp_c" in cleaned
    assert "dewp_f" in cleaned
    assert "flight_category" in cleaned
    assert cleaned["flight_category"] in {"VFR", "MVFR", "IFR", "LIFR"}
    assert "is_stale" in cleaned
    assert "raw_metar" in cleaned
    assert "altim_inhg" in cleaned
    assert "wdir_cardinal" in cleaned
    assert "plain_english" in cleaned
    assert isinstance(cleaned["plain_english"], str)
    assert len(cleaned["plain_english"]) > 10
    assert "headline" in cleaned
    assert isinstance(cleaned["headline"], str)
    assert "sam_briefing" in cleaned
    assert isinstance(cleaned["sam_briefing"], str)
    assert len(cleaned["sam_briefing"]) > 10
    assert "sam_headline" in cleaned
    assert isinstance(cleaned["sam_headline"], str)

    # Check temperature conversion accuracy
    if cleaned["temp_c"] is not None:
        expected_f = round(cleaned["temp_c"] * 9 / 5 + 32, 1)
        assert cleaned["temp_f"] == expected_f


def test_clean_sample_taf(sample_taf_data):
    """Test cleaning against real saved sample TAF data."""
    assert len(sample_taf_data) > 0
    raw_sample = sample_taf_data[0]
    cleaned = weather.clean_taf_record(raw_sample)

    assert "icao" in cleaned
    assert "forecasts" in cleaned
    assert isinstance(cleaned["forecasts"], list)
    if cleaned["forecasts"]:
        fcst = cleaned["forecasts"][0]
        assert "time_from" in fcst
        assert "flight_category" in fcst
        assert "visib_display" in fcst


def test_flight_category_inference_when_missing():
    """Test filling in missing flight category based on ceiling and visibility."""
    # LIFR due to low ceiling (<500)
    record_lifr_ceil = {
        "icaoId": "TEST",
        "clouds": [{"cover": "OVC", "base": 400}],
        "visib": "10",
        "fltCat": None
    }
    cleaned = weather.clean_metar_record(record_lifr_ceil)
    assert cleaned["flight_category"] == "LIFR"

    # LIFR due to low visibility (<1 SM)
    record_lifr_vis = {
        "icaoId": "TEST",
        "clouds": [{"cover": "SCT", "base": 5000}],
        "visib": "1/2",
        "fltCat": ""
    }
    cleaned = weather.clean_metar_record(record_lifr_vis)
    assert cleaned["flight_category"] == "LIFR"

    # IFR (ceiling 500-999 or vis 1 to <3)
    record_ifr = {
        "icaoId": "TEST",
        "clouds": [{"cover": "BKN", "base": 800}],
        "visib": "5",
        "fltCat": None
    }
    cleaned = weather.clean_metar_record(record_ifr)
    assert cleaned["flight_category"] == "IFR"

    # MVFR (ceiling 1000-3000 or vis 3 to 5)
    record_mvfr = {
        "icaoId": "TEST",
        "clouds": [{"cover": "BKN", "base": 2500}],
        "visib": "6",
        "fltCat": None
    }
    cleaned = weather.clean_metar_record(record_mvfr)
    assert cleaned["flight_category"] == "MVFR"

    # VFR (ceiling >3000 and vis >5)
    record_vfr = {
        "icaoId": "TEST",
        "clouds": [{"cover": "FEW", "base": 5000}],
        "visib": "10+",
        "fltCat": None
    }
    cleaned = weather.clean_metar_record(record_vfr)
    assert cleaned["flight_category"] == "VFR"


def test_stale_report_detection():
    """Test marking stale reports if older than 75 minutes."""
    now = time.time()

    # Fresh report (15 minutes old)
    fresh_record = {
        "icaoId": "KJFK",
        "obsTime": now - (15 * 60),
        "rawOb": "METAR KJFK 120000Z 10005KT"
    }
    cleaned_fresh = weather.clean_metar_record(fresh_record, current_timestamp=now)
    assert cleaned_fresh["is_stale"] is False
    assert cleaned_fresh["age_minutes"] == 15

    # Stale report (90 minutes old)
    stale_record = {
        "icaoId": "KJFK",
        "obsTime": now - (90 * 60),
        "rawOb": "METAR KJFK 120000Z 10005KT"
    }
    cleaned_stale = weather.clean_metar_record(stale_record, current_timestamp=now)
    assert cleaned_stale["is_stale"] is True
    assert cleaned_stale["age_minutes"] == 90


def test_visibility_parsing():
    """Test parsing fraction and plus strings into statute miles."""
    assert weather.parse_visibility_statute_miles("10+") == 10.0
    assert weather.parse_visibility_statute_miles("6+") == 6.0
    assert weather.parse_visibility_statute_miles("1/2") == 0.5
    assert weather.parse_visibility_statute_miles("1 1/2") == 1.5
    assert weather.parse_visibility_statute_miles("3/4") == 0.75
    assert weather.parse_visibility_statute_miles(10) == 10.0
    assert weather.parse_visibility_statute_miles(None) is None


def test_in_memory_cache():
    """Test that weather cache stores and returns data within TTL."""
    cache = weather.WeatherCache(ttl_seconds=1)
    cache.set("key1", {"data": "test"})

    assert cache.get("key1") == {"data": "test"}

    # Sleep past TTL
    time.sleep(1.05)
    assert cache.get("key1") is None


@patch("weather.fetch_and_clean_metar")
def test_get_metar_caching(mock_fetch, sample_metar_data):
    """Test that get_metar calls upstream once and hits cache on second call."""
    mock_fetch.return_value = [weather.clean_metar_record(sample_metar_data[0])]
    weather._metar_cache.clear()

    # First call: hits fetch
    res1 = weather.get_metar("KMSP")
    assert res1["cached"] is False
    assert mock_fetch.call_count == 1

    # Second call within 5m: hits cache
    res2 = weather.get_metar("KMSP")
    assert res2["cached"] is True
    assert mock_fetch.call_count == 1


def test_flask_routes(client, sample_metar_data, sample_taf_data):
    """Test Flask web and API routes."""
    # Test index route
    res_index = client.get("/")
    assert res_index.status_code == 200

    # Test /api/metar missing ids
    res_missing_metar = client.get("/api/metar")
    assert res_missing_metar.status_code == 400

    # Test /api/taf missing ids
    res_missing_taf = client.get("/api/taf")
    assert res_missing_taf.status_code == 400

    # Test /api/metar with valid mocked data
    with patch("weather.fetch_and_clean_metar") as mock_metar:
        mock_metar.return_value = [weather.clean_metar_record(sample_metar_data[0])]
        weather._metar_cache.clear()
        res = client.get("/api/metar?ids=KDEN")
        assert res.status_code == 200
        payload = res.get_json()
        assert "data" in payload
        assert payload["data"][0]["icao"] == "KDEN"


def test_normalize_station_ids():
    """Test station ID normalization, deduplication, and spacing."""
    assert weather.normalize_station_ids("kmsp, kjfk   kden,KMSP") == "KMSP,KJFK,KDEN"
    assert weather.normalize_station_ids(["kmsp", "kjfk", "kmsp"]) == "KMSP,KJFK"
    assert weather.normalize_station_ids("") == ""


@patch("weather.fetch_and_clean_taf")
def test_get_taf_caching(mock_fetch, sample_taf_data):
    """Test that get_taf uses cache on subsequent calls."""
    mock_fetch.return_value = [weather.clean_taf_record(sample_taf_data[0])]
    weather._taf_cache.clear()

    res1 = weather.get_taf("KMSP")
    assert res1["cached"] is False
    assert mock_fetch.call_count == 1

    res2 = weather.get_taf("KMSP")
    assert res2["cached"] is True
    assert mock_fetch.call_count == 1


def test_api_upstream_error_handling(client):
    """Test 502 response when upstream service fails."""
    import requests

    with patch("weather.requests.get") as mock_get:
        mock_get.side_effect = requests.RequestException("Connection timeout")
        weather._metar_cache.clear()
        weather._taf_cache.clear()

        res_metar = client.get("/api/metar?ids=KMSP")
        assert res_metar.status_code == 502
        assert "error" in res_metar.get_json()

        res_taf = client.get("/api/taf?ids=KMSP")
        assert res_taf.status_code == 502
        assert "error" in res_taf.get_json()

