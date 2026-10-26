"""
main.py - Thin Flask routing layer for AVWX.
Routes:
  GET /           -> Serves frontend UI
  GET /api/metar  -> Fetches & cleans METAR data for specified IDs
  GET /api/taf    -> Fetches & cleans TAF data for specified IDs
"""

import os
import requests
from flask import Flask, jsonify, request, send_from_directory
import weather

app = Flask(__name__, static_folder="static", static_url_path="")


@app.route("/")
def index():
    """Serve the single-page application entry point."""
    return send_from_directory("static", "index.html")


@app.route("/api/metar")
def get_metar_route():
    """
    Route for METAR reports.
    Query param: ?ids=KMSP,KJFK,KORD
    """
    ids = request.args.get("ids", "").strip()
    if not ids:
        return jsonify({"error": "Missing required query parameter 'ids'"}), 400

    try:
        result = weather.get_metar(ids)
        return jsonify(result), 200
    except requests.RequestException as exc:
        return jsonify({
            "error": "Failed to fetch weather data from upstream aviationweather.gov",
            "details": str(exc)
        }), 502
    except Exception as exc:
        return jsonify({"error": "Internal server error", "details": str(exc)}), 500


@app.route("/api/taf")
def get_taf_route():
    """
    Route for TAF forecasts.
    Query param: ?ids=KMSP,KJFK,KORD
    """
    ids = request.args.get("ids", "").strip()
    if not ids:
        return jsonify({"error": "Missing required query parameter 'ids'"}), 400

    try:
        result = weather.get_taf(ids)
        return jsonify(result), 200
    except requests.RequestException as exc:
        return jsonify({
            "error": "Failed to fetch forecast data from upstream aviationweather.gov",
            "details": str(exc)
        }), 502
    except Exception as exc:
        return jsonify({"error": "Internal server error", "details": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
