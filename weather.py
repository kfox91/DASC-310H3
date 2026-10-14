"""
weather.py - Fetching, cleaning, and caching aviation weather data.
Fetches METAR and TAF from aviationweather.gov Data API.
Caches responses in memory for 5 minutes.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Aviation Weather Data API endpoints
METAR_API_URL = "https://aviationweather.gov/api/data/metar"
TAF_API_URL = "https://aviationweather.gov/api/data/taf"
USER_AGENT = "AVWX-Service/1.0 (+https://cloud.google.com/run; aviation-weather-monitor)"
REQUEST_TIMEOUT = 10  # seconds
CACHE_TTL_SECONDS = 300  # 5 minutes
STALE_THRESHOLD_MINUTES = 75  # Reports older than 75 minutes marked stale


class WeatherCache:
    """Simple thread-safe in-memory cache with time-to-live (TTL)."""

    def __init__(self, ttl_seconds: int = CACHE_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            cached_time, value = entry
            if time.time() - cached_time < self.ttl_seconds:
                return value
            # Expired
            del self._cache[key]
            return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = (time.time(), value)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# Shared cache instances
_metar_cache = WeatherCache(CACHE_TTL_SECONDS)
_taf_cache = WeatherCache(CACHE_TTL_SECONDS)


def normalize_station_ids(ids: str | List[str]) -> str:
    """Normalize comma/space separated station list to sorted, uppercase comma string."""
    if isinstance(ids, list):
        raw_list = ids
    else:
        # Split on commas, spaces, or semicolons
        raw_list = re.split(r"[\s,;]+", str(ids).strip())
    clean_ids = [code.strip().upper() for code in raw_list if code.strip()]
    # Deduplicate while preserving order or sorting for consistent cache key
    seen = set()
    deduped = []
    for item in clean_ids:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return ",".join(deduped)


def degrees_to_cardinal(deg: Optional[int | float | str]) -> str:
    """Convert wind direction in degrees to 16-point cardinal compass string."""
    if deg is None:
        return "VRB"
    try:
        val = float(deg) % 360
    except (ValueError, TypeError):
        return str(deg).upper() if str(deg).strip() else "VRB"

    directions = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
    ]
    idx = int((val + 11.25) / 22.5) % 16
    return directions[idx]


def parse_visibility_statute_miles(vis_val: Any) -> Optional[float]:
    """Parse various visibility string/number formats (e.g. '10+', '6+', '1/2', '1 1/2', 10) into float miles."""
    if vis_val is None:
        return None
    if isinstance(vis_val, (int, float)):
        return float(vis_val)

    s = str(vis_val).strip().rstrip("+").strip()
    if not s:
        return None

    try:
        # Handle mixed fractions like '1 1/2'
        parts = s.split()
        if len(parts) == 2 and "/" in parts[1]:
            whole = float(parts[0])
            num, denom = parts[1].split("/")
            return whole + (float(num) / float(denom))
        if "/" in s:
            num, denom = s.split("/")
            return float(num) / float(denom)
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None


def calculate_ceiling(clouds: Optional[List[Dict[str, Any]]]) -> Optional[int]:
    """Calculate lowest ceiling in feet AGL from cloud layers (BKN, OVC, or VV)."""
    if not clouds:
        return None
    ceiling_bases = []
    for layer in clouds:
        cover = (layer.get("cover") or "").upper()
        base = layer.get("base")
        if cover in {"BKN", "OVC", "VV"} and base is not None:
            try:
                ceiling_bases.append(int(base))
            except (ValueError, TypeError):
                continue
    return min(ceiling_bases) if ceiling_bases else None


def compute_flight_category(ceiling_ft: Optional[int], visib_sm: Optional[float]) -> str:
    """
    Standard FAA Flight Category rules:
    - LIFR: ceiling < 500 ft OR visibility < 1 statute mile
    - IFR:  ceiling 500 to < 1000 ft OR visibility 1 to < 3 statute miles
    - MVFR: ceiling 1000 to 3000 ft OR visibility 3 to 5 statute miles
    - VFR:  ceiling > 3000 ft (or none/unlimited) AND visibility > 5 statute miles
    """
    # Low IFR
    if (ceiling_ft is not None and ceiling_ft < 500) or (visib_sm is not None and visib_sm < 1.0):
        return "LIFR"
    # IFR
    if (ceiling_ft is not None and ceiling_ft < 1000) or (visib_sm is not None and visib_sm < 3.0):
        return "IFR"
    # MVFR
    if (ceiling_ft is not None and ceiling_ft <= 3000) or (visib_sm is not None and visib_sm <= 5.0):
        return "MVFR"
    # Default VFR if not meeting lower categories
    return "VFR"


DIRECTION_NAMES: Dict[str, str] = {
    "N": "the north", "NNE": "the north-northeast", "NE": "the northeast", "ENE": "the east-northeast",
    "E": "the east", "ESE": "the east-southeast", "SE": "the southeast", "SSE": "the south-southeast",
    "S": "the south", "SSW": "the south-southwest", "SW": "the southwest", "WSW": "the west-southwest",
    "W": "the west", "WNW": "the west-northwest", "NW": "the northwest", "NNW": "the north-northwest",
    "VRB": "variable directions"
}

WEATHER_CODE_NAMES: Dict[str, str] = {
    "+TSRA": "heavy thunderstorms and rain",
    "-TSRA": "light thunderstorms and rain",
    "TSRA": "thunderstorms with rain",
    "+SN": "heavy snow",
    "-SN": "light snow",
    "SN": "snow",
    "+RA": "heavy rain",
    "-RA": "light rain",
    "RA": "steady rain",
    "-DZ": "light drizzle",
    "DZ": "drizzle",
    "TS": "thunderstorms",
    "FZRA": "freezing rain",
    "FZDZ": "freezing drizzle",
    "PL": "ice pellets",
    "FG": "dense fog",
    "BR": "mist",
    "HZ": "haze",
    "FU": "smoke",
    "SQ": "squalls",
    "BLSN": "blowing snow"
}


def generate_summary_headline(
    flt_cat: str,
    ceiling_ft: Optional[int],
    visib_sm: Optional[float],
    wspd: Optional[int],
    wgst: Optional[int]
) -> str:
    """Produce a concise, plain-English status headline."""
    if flt_cat == "LIFR":
        if ceiling_ft and ceiling_ft < 500:
            return f"Low Ceiling ({ceiling_ft:,} ft) • LIFR"
        return "Low Visibility • LIFR"
    elif flt_cat == "IFR":
        if ceiling_ft and ceiling_ft < 1000:
            return f"Ceiling at {ceiling_ft:,} ft • IFR"
        return "Reduced Visibility • IFR"
    elif flt_cat == "MVFR":
        if ceiling_ft:
            return f"Marginal Ceiling ({ceiling_ft:,} ft) • MVFR"
        return "Marginal Visibility • MVFR"
    else:
        # VFR
        if wgst:
            return f"VFR • Gusty Winds ({wgst} kt)"
        elif wspd and wspd >= 15:
            return f"VFR • Brisk Winds ({wspd} kt)"
        elif wspd and wspd > 0:
            return f"VFR • Light {wspd} kt Breeze"
        else:
            return "VFR • Calm & Clear"


def generate_conversational_summary(*args, **kwargs) -> str:
    """Fallback alias pointing directly to Sam Elliott briefing."""
    return generate_sam_elliott_briefing(*args, **kwargs)


def generate_sam_headline(
    flt_cat: str,
    ceiling_ft: Optional[int],
    wspd: Optional[int],
    wgst: Optional[int]
) -> str:
    """Produce folksy, honest headline without 'Sam's Take' prefix."""
    if flt_cat == "LIFR":
        return "Thick Soup — Tie Her Down Tight"
    elif flt_cat == "IFR":
        return "Gauge Weather — Stay by the Hangar"
    elif flt_cat == "MVFR":
        return "Watch That Altitude, Partner"
    elif wgst and wgst >= 20:
        return f"Mind That {wgst} kt Gust on Final"
    elif wspd and wspd >= 15:
        return f"Brisk {wspd} kt Blow Out There"
    elif wspd and wspd > 0:
        return f"Mighty Fine VFR Day to Fly"
    else:
        return "Clear Skies, Windsock's Sleepin'"


def generate_sam_elliott_briefing(
    flt_cat: str,
    temp_f: Optional[float],
    temp_c: Optional[float],
    dewp_f: Optional[float],
    wspd: Optional[int],
    wgst: Optional[int],
    wdir_cardinal: str,
    visib_sm: Optional[float],
    ceiling_ft: Optional[int],
    clouds: List[Dict[str, Any]],
    raw_ob: str,
    altim_inhg: Optional[float]
) -> str:
    """
    Generate a weather briefing delivered with the slow, weathered,
    reassuring drawl of Sam Elliott — folksy, but still strictly informational.
    """
    lines = []

    # 1. Opening verdict
    if flt_cat == "LIFR":
        lines.append("Ain't no place to be foolin' around up there today — you're lookin' at low IFR (LIFR).")
    elif flt_cat == "IFR":
        lines.append("Now look here, partner, you got yourself some genuine instrument weather (IFR).")
    elif flt_cat == "MVFR":
        lines.append("Well now, things are lookin' a touch marginal out there (MVFR).")
    elif wgst and wgst >= 20:
        lines.append("She's visual flying weather (VFR), but don't you go gettin' too comfortable in that seat.")
    else:
        lines.append("Well now, take a look at that. Mighty fine visual flying conditions (VFR).")

    # 2. Sky & visibility
    sky_parts = []
    if ceiling_ft is not None:
        sky_parts.append(f"Got a cloud deck hangin' down at {ceiling_ft:,} feet")
    elif clouds:
        lowest = next((c.get("base") for c in clouds if c.get("base") is not None), None)
        if lowest:
            sky_parts.append(f"Just a few clouds driftin' by around {lowest:,} feet, nothin' to fret over")
        else:
            sky_parts.append("A few scattered clouds driftin' through with plenty of blue above")
    else:
        sky_parts.append("Sky's wide open, clear as a mountain creek")

    if visib_sm is not None:
        if visib_sm >= 10:
            sky_parts.append("visibility clean as a whistle past 10 miles")
        else:
            sky_parts.append(f"visibility shaved down to {visib_sm:g} miles")

    # Phenomena & remarks (e.g. RAB, SN, TS)
    if "TS" in raw_ob:
        sky_parts.append("with thunder rumblin' around — give that a wide berth")
    elif "+RA" in raw_ob:
        sky_parts.append("with a heavy rain pourin' across the field")
    elif "RA" in raw_ob:
        sky_parts.append("with rain fallin' steady")
    elif "SN" in raw_ob:
        sky_parts.append("with snow comin' down")
    elif "FG" in raw_ob:
        sky_parts.append("with thick fog sittin' heavy")
    elif "BR" in raw_ob:
        sky_parts.append("with mist hangin' in the air")
    elif "HZ" in raw_ob:
        sky_parts.append("with haze softenin' the horizon")

    rab_match = re.search(r'\bRAB(\d{2})?\b', raw_ob)
    if rab_match:
        if rab_match.group(1):
            sky_parts.append(f"rain got started about {rab_match.group(1)} past the hour")
        else:
            sky_parts.append("looks like that rain just got started")

    lines.append(", and ".join(sky_parts).capitalize() + ".")

    # 3. Wind
    dir_name = DIRECTION_NAMES.get(wdir_cardinal, "the wind")
    if wspd is None or wspd == 0:
        lines.append("Winds are dead calm — windsock's just sleepin'.")
    elif wgst:
        lines.append(f"Wind's pushin' out of {dir_name} at {wspd} knots, gustin' up to {wgst}. She'll test your stick-and-rudder work on touchdown, so keep both hands on the yoke.")
    elif wspd >= 15:
        lines.append(f"Got a brisk {wspd}-knot blow comin' off {dir_name}. Steady hands on final.")
    elif wspd >= 7:
        lines.append(f"Got a steady little {wspd}-knot breeze comin' out of {dir_name}.")
    else:
        lines.append(f"Just an easy {wspd}-knot breeze wanderin' in from {dir_name}.")

    # 4. Temperature, Altimeter & Sign-off
    tail = []
    if temp_f is not None and temp_c is not None:
        tail.append(f"Thermometer reads {temp_f:.0f}°F ({temp_c:.0f}°C)")
    if altim_inhg is not None:
        tail.append(f"altimeter sits at {altim_inhg:.2f} inHg")

    signoff = "Keep her right-side up, and fly safe out there."
    if flt_cat in ("IFR", "LIFR"):
        signoff = "If you ain't sharp on your gauges, best stay parked by the hangar and pour another cup of coffee."
    elif flt_cat == "MVFR":
        signoff = "Keep your eyes outside, mind your altitude, and don't go scuddin' under those clouds."
    elif wgst and wgst >= 20:
        signoff = "Fly safe, watch your crosswind, and make that landing count."

    if tail:
        lines.append(", ".join(tail).capitalize() + f". {signoff}")
    else:
        lines.append(signoff)

    return " ".join(lines)


def clean_metar_record(raw: Dict[str, Any], current_timestamp: Optional[float] = None) -> Dict[str, Any]:
    """
    Cleans and standardizes a single raw METAR record from aviationweather.gov.
    - Converts temperatures (C to F)
    - Converts altimeter (hPa to inHg)
    - Computes cardinal wind direction & speeds
    - Computes ceiling & verifies/fills flight category (VFR/MVFR/IFR/LIFR)
    - Computes report age & stale flag
    - Returns consistent, null-safe schema
    """
    now = current_timestamp if current_timestamp is not None else time.time()

    # Temperature & Dewpoint
    temp_c = raw.get("temp")
    dewp_c = raw.get("dewp")
    temp_f = round(temp_c * 9 / 5 + 32, 1) if temp_c is not None else None
    dewp_f = round(dewp_c * 9 / 5 + 32, 1) if dewp_c is not None else None
    temp_spread_c = round(temp_c - dewp_c, 1) if (temp_c is not None and dewp_c is not None) else None

    # Wind
    wdir = raw.get("wdir")
    wspd = raw.get("wspd")
    wgst = raw.get("wgst")
    wdir_cardinal = degrees_to_cardinal(wdir) if wdir is not None else "VRB"
    wspd_mph = round(wspd * 1.15078) if wspd is not None else None
    wgst_mph = round(wgst * 1.15078) if wgst is not None else None

    # Altimeter
    altim_hpa = raw.get("altim")
    altim_inhg: Optional[float] = None
    if altim_hpa is not None:
        altim_inhg = round(altim_hpa * 0.0295299830714, 2)
    # Check if rawOb has explicit AXXXX (e.g. A2992 -> 29.92 inHg) for standard accuracy
    raw_ob = raw.get("rawOb") or ""
    altim_match = re.search(r"\bA(\d{4})\b", raw_ob)
    if altim_match:
        try:
            altim_inhg = float(altim_match.group(1)) / 100.0
        except ValueError:
            pass

    # Visibility
    raw_vis = raw.get("visib")
    visib_sm = parse_visibility_statute_miles(raw_vis)
    visib_text = f"{raw_vis} SM" if raw_vis is not None else "N/A"

    # Clouds & Ceiling
    clouds = raw.get("clouds") or []
    cleaned_clouds = []
    for c in clouds:
        cleaned_clouds.append({
            "cover": c.get("cover") or "UNK",
            "base": c.get("base"),
            "type": c.get("type")
        })
    ceiling_ft = calculate_ceiling(cleaned_clouds)

    # Flight Category: fill in if missing or empty
    flt_cat = (raw.get("fltCat") or "").strip().upper()
    if flt_cat not in {"VFR", "MVFR", "IFR", "LIFR"}:
        flt_cat = compute_flight_category(ceiling_ft, visib_sm)

    # Stale calculation
    obs_time = raw.get("obsTime")
    age_minutes: Optional[int] = None
    is_stale = False
    obs_time_iso = raw.get("reportTime") or raw.get("receiptTime")

    if obs_time is not None:
        age_seconds = max(0.0, now - float(obs_time))
        age_minutes = int(age_seconds // 60)
        is_stale = age_minutes >= STALE_THRESHOLD_MINUTES
        if not obs_time_iso:
            obs_time_iso = datetime.fromtimestamp(obs_time, tz=timezone.utc).isoformat()
    elif obs_time_iso:
        # Fallback to parsing ISO string if obsTime not given
        try:
            dt = datetime.fromisoformat(obs_time_iso.replace("Z", "+00:00"))
            age_seconds = max(0.0, now - dt.timestamp())
            age_minutes = int(age_seconds // 60)
            is_stale = age_minutes >= STALE_THRESHOLD_MINUTES
        except Exception:
            pass

    # Elevation
    elev_m = raw.get("elev")
    elev_ft = round(elev_m * 3.28084) if elev_m is not None else None

    # Sam Elliott Briefing
    briefing = generate_sam_elliott_briefing(
        flt_cat=flt_cat,
        temp_f=temp_f,
        temp_c=temp_c,
        dewp_f=dewp_f,
        wspd=wspd,
        wgst=wgst,
        wdir_cardinal=wdir_cardinal,
        visib_sm=visib_sm,
        ceiling_ft=ceiling_ft,
        clouds=cleaned_clouds,
        raw_ob=raw_ob,
        altim_inhg=altim_inhg
    )
    headline = generate_sam_headline(flt_cat, ceiling_ft, wspd, wgst)

    return {
        "icao": raw.get("icaoId") or raw.get("stationId") or "UNKNOWN",
        "name": raw.get("name") or "Unknown Station",
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "elev_m": elev_m,
        "elev_ft": elev_ft,
        "obs_time": obs_time,
        "obs_time_iso": obs_time_iso,
        "age_minutes": age_minutes,
        "is_stale": is_stale,
        "flight_category": flt_cat,
        "headline": headline,
        "briefing": briefing,
        "plain_english": briefing,
        "sam_briefing": briefing,
        "sam_headline": headline,
        "temp_c": temp_c,
        "temp_f": temp_f,
        "dewp_c": dewp_c,
        "dewp_f": dewp_f,
        "temp_spread_c": temp_spread_c,
        "wdir": wdir,
        "wdir_cardinal": wdir_cardinal,
        "wspd_kt": wspd,
        "wspd_mph": wspd_mph,
        "wgst_kt": wgst,
        "wgst_mph": wgst_mph,
        "altim_hpa": altim_hpa,
        "altim_inhg": altim_inhg,
        "slp_hpa": raw.get("slp"),
        "visib_sm": visib_sm,
        "visib_display": visib_text,
        "cover": raw.get("cover") or (cleaned_clouds[0]["cover"] if cleaned_clouds else "CLR"),
        "ceiling_ft": ceiling_ft,
        "clouds": cleaned_clouds,
        "raw_metar": raw_ob,
        "metar_type": raw.get("metarType") or "METAR",
    }


def clean_taf_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Cleans and standardizes a single raw TAF record."""
    raw_fcsts = raw.get("fcsts") or []
    cleaned_fcsts = []

    for f in raw_fcsts:
        time_from = f.get("timeFrom")
        time_to = f.get("timeTo")
        time_from_iso = datetime.fromtimestamp(time_from, tz=timezone.utc).isoformat() if time_from else None
        time_to_iso = datetime.fromtimestamp(time_to, tz=timezone.utc).isoformat() if time_to else None

        clouds = []
        for c in (f.get("clouds") or []):
            clouds.append({
                "cover": c.get("cover") or "SKC",
                "base": c.get("base"),
                "type": c.get("type")
            })

        ceiling_ft = calculate_ceiling(clouds)
        visib_sm = parse_visibility_statute_miles(f.get("visib"))
        flt_cat = compute_flight_category(ceiling_ft, visib_sm)

        wdir = f.get("wdir")
        wdir_cardinal = degrees_to_cardinal(wdir) if wdir is not None else "VRB"

        cleaned_fcsts.append({
            "time_from": time_from,
            "time_to": time_to,
            "time_from_iso": time_from_iso,
            "time_to_iso": time_to_iso,
            "fcst_change": f.get("fcstChange") or "BASE",
            "probability": f.get("probability"),
            "wdir": wdir,
            "wdir_cardinal": wdir_cardinal,
            "wspd_kt": f.get("wspd"),
            "wgst_kt": f.get("wgst"),
            "visib_sm": visib_sm,
            "visib_display": f"{f.get('visib')} SM" if f.get("visib") is not None else "N/A",
            "wx_string": f.get("wxString"),
            "ceiling_ft": ceiling_ft,
            "clouds": clouds,
            "flight_category": flt_cat,
        })

    elev_m = raw.get("elev")
    elev_ft = round(elev_m * 3.28084) if elev_m is not None else None

    return {
        "icao": raw.get("icaoId") or "UNKNOWN",
        "name": raw.get("name") or "Unknown Station",
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "elev_m": elev_m,
        "elev_ft": elev_ft,
        "issue_time": raw.get("issueTime"),
        "valid_from": raw.get("validTimeFrom"),
        "valid_to": raw.get("validTimeTo"),
        "raw_taf": raw.get("rawTAF") or "",
        "forecasts": cleaned_fcsts,
    }


def fetch_and_clean_metar(ids: str) -> List[Dict[str, Any]]:
    """
    Fetch METAR reports for given station IDs from aviationweather.gov,
    clean the data, and return standardized list.
    """
    normalized = normalize_station_ids(ids)
    if not normalized:
        return []

    headers = {"User-Agent": USER_AGENT}
    params = {"ids": normalized, "format": "json"}

    response = requests.get(METAR_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        return []

    cleaned = [clean_metar_record(item) for item in data]
    return cleaned


def fetch_and_clean_taf(ids: str) -> List[Dict[str, Any]]:
    """
    Fetch TAF reports for given station IDs from aviationweather.gov,
    clean the data, and return standardized list.
    """
    normalized = normalize_station_ids(ids)
    if not normalized:
        return []

    headers = {"User-Agent": USER_AGENT}
    params = {"ids": normalized, "format": "json"}

    response = requests.get(TAF_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        return []

    cleaned = [clean_taf_record(item) for item in data]
    return cleaned


def get_metar(ids: str) -> Dict[str, Any]:
    """
    High-level entrypoint for METAR:
    Returns cached results if available within 5 minutes, otherwise fetches and cleans.
    """
    normalized = normalize_station_ids(ids)
    if not normalized:
        return {"data": [], "cached": False, "count": 0, "query": ids}

    cache_key = f"metar:{normalized}"
    cached_data = _metar_cache.get(cache_key)
    if cached_data is not None:
        return {
            "data": cached_data,
            "cached": True,
            "count": len(cached_data),
            "query": normalized
        }

    data = fetch_and_clean_metar(normalized)
    _metar_cache.set(cache_key, data)

    return {
        "data": data,
        "cached": False,
        "count": len(data),
        "query": normalized
    }


def get_taf(ids: str) -> Dict[str, Any]:
    """
    High-level entrypoint for TAF:
    Returns cached results if available within 5 minutes, otherwise fetches and cleans.
    """
    normalized = normalize_station_ids(ids)
    if not normalized:
        return {"data": [], "cached": False, "count": 0, "query": ids}

    cache_key = f"taf:{normalized}"
    cached_data = _taf_cache.get(cache_key)
    if cached_data is not None:
        return {
            "data": cached_data,
            "cached": True,
            "count": len(cached_data),
            "query": normalized
        }

    data = fetch_and_clean_taf(normalized)
    _taf_cache.set(cache_key, data)

    return {
        "data": data,
        "cached": False,
        "count": len(data),
        "query": normalized
    }
