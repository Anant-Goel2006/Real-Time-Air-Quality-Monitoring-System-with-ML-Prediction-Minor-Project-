"""
Air Quality Monitoring Dashboard — Flask Backend
Tech: Python | Flask | ML (Random Forest) | Pandas | NumPy | REST API
WAQI Live API: https://api.waqi.info
"""

import os, json, re, math
from datetime import datetime
import time
from urllib.parse import quote
from flask import Flask, render_template, jsonify, request, send_from_directory, make_response
import pandas as pd
import numpy as np
import requests
from dotenv import load_dotenv

# ── Load .env ─────────────────────────────────────────────────
load_dotenv()
DEFAULT_WAQI_TOKEN = "5d7668773d931a595fbdaeabb00bfd65481b8db3"

def resolve_waqi_token():
    candidates = [
        ("WAQI_API_TOKEN", os.getenv("WAQI_API_TOKEN")),
        ("WAQI_TOKEN", os.getenv("WAQI_TOKEN")),
        ("default", DEFAULT_WAQI_TOKEN),
    ]
    invalid_tokens = {
        "",
        "your_waqi_api_token",
        "your_waqi_token",
        "replace-me",
        "changeme",
    }
    for source, raw in candidates:
        cleaned = str(raw or "").strip().strip('"').strip("'")
        if cleaned.lower() in invalid_tokens:
            continue
        if cleaned:
            return cleaned, source
    return "", "missing"

WAQI_TOKEN, WAQI_TOKEN_SOURCE = resolve_waqi_token()
WAQI_BASE_URL = os.getenv("WAQI_BASE_URL", "https://api.waqi.info").strip().rstrip("/")
if WAQI_BASE_URL == "http://api.waqi.info":
    WAQI_BASE_URL = "https://api.waqi.info"
FLASK_PORT    = int(os.getenv("FLASK_PORT",  8080))
FLASK_DEBUG   = os.getenv("FLASK_DEBUG", "False").lower() == "true"
APP_BUILD_TS  = int(time.time())

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# ── Global state ───────────────────────────────────────────────
df       = None
model    = None
scaler   = None
encoders = None
LIVE_FEED_CACHE = {}
LIVE_CACHE_TTL_SEC = int(os.getenv("LIVE_CACHE_TTL_SEC", "1800"))

# ── AQI categories (EPA standard + aqi.in labels) ─────────────
AQI_CATEGORIES = [
    {"min":0,   "max":50,  "level":"Good",            "color":"#009966", "bg":"#e8f8f2", "text":"The air is fresh and free from toxins. Enjoy outdoor activities without any health concerns."},
    {"min":51,  "max":100, "level":"Moderate",         "color":"#ffde33", "bg":"#fffde8", "text":"Air quality is acceptable for most, but sensitive individuals might experience mild discomfort."},
    {"min":101, "max":150, "level":"Poor",              "color":"#ff9933", "bg":"#fff3e0", "text":"Breathing may become slightly uncomfortable, especially for those with respiratory issues."},
    {"min":151, "max":200, "level":"Unhealthy",         "color":"#cc0033", "bg":"#fde8ed", "text":"Risky for children, pregnant women, and the elderly. Limit outdoor activities."},
    {"min":201, "max":300, "level":"Severe",            "color":"#660099", "bg":"#f3e8ff", "text":"Prolonged exposure can cause chronic health issues or organ damage. Avoid outdoor activities."},
    {"min":301, "max":999, "level":"Hazardous",         "color":"#7e0023", "bg":"#fde8e8", "text":"Dangerously high pollution levels. Life-threatening health risks. Stay indoors."},
]

def get_category(aqi_value):
    for c in AQI_CATEGORIES:
        if c["min"] <= aqi_value <= c["max"]:
            return c
    return AQI_CATEGORIES[-1]

def safe_float(raw, default=0.0):
    try:
        return float(raw)
    except Exception:
        return float(default)

LIVE_QUERY_ALIASES = {
    "uk": "united kingdom",
    "england": "united kingdom",
    "usa": "united states",
    "us": "united states",
    "u s": "united states",
    "u.s.": "united states",
}

STATE_HINTS = {
    "new delhi", "delhi", "maharashtra", "karnataka", "tamil nadu", "telangana",
    "west bengal", "uttar pradesh", "haryana", "california", "illinois", "new york",
    "england", "scotland", "wales", "tokyo", "beijing", "shanghai", "nsw", "vic",
}

COUNTRY_CODE_MAP = {
    "in": "India",
    "us": "United States",
    "gb": "United Kingdom",
    "uk": "United Kingdom",
    "fr": "France",
    "au": "Australia",
    "sg": "Singapore",
    "cn": "China",
    "jp": "Japan",
}

AREA_HINTS = {
    "road", "rd", "street", "st", "sector", "phase", "block", "nagar", "colony",
    "market", "airport", "industrial", "college", "school", "hospital", "chowk",
    "junction", "square", "zone", "park", "ward", "township", "district",
}


def normalize_query_text(raw):
    txt = str(raw or "").strip()
    txt = txt.replace("+", " ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def normalize_live_query(raw):
    txt = normalize_query_text(raw)
    txt_l = txt.lower()
    if re.fullmatch(r"@?\d+", txt_l or ""):
        return f"@{txt_l.lstrip('@')}", txt
    canonical = LIVE_QUERY_ALIASES.get(txt_l, txt_l)
    return canonical, txt


def encode_feed_query(query):
    return quote(str(query or "").strip(), safe="@:;,-._~")


def waqi_get_json(path, params=None, timeout=8, log_label="WAQI"):
    query = dict(params or {})
    query["token"] = WAQI_TOKEN
    endpoint = f"{WAQI_BASE_URL}{path}"
    safe_query = "&".join(
        f"{k}={'***' if k == 'token' else v}" for k, v in query.items()
    )
    print(f"→ {log_label}: {endpoint}?{safe_query}")
    try:
        resp = requests.get(endpoint, params=query, timeout=timeout)
        try:
            payload = resp.json()
        except Exception as json_err:
            print(f"⚠ JSON parse error: {json_err}")
            payload = {"status": "error", "data": "Invalid WAQI response"}
        return payload, resp.status_code
    except requests.exceptions.ConnectionError:
        print(f"⚠ Connection error")
        return {"status": "error", "data": "Cannot reach api.waqi.info"}, 503
    except requests.exceptions.Timeout:
        print(f"⚠ Timeout error")
        return {"status": "error", "data": "WAQI API timeout"}, 504
    except Exception as e:
        print(f"⚠ Unexpected error: {e}")
        return {"status": "error", "data": str(e)}, 500


def fetch_feed(query, timeout=8):
    q = normalize_query_text(query)
    if not q:
        return {"status": "error", "data": "Empty station query"}, 400
    payload, code = waqi_get_json(f"/feed/{encode_feed_query(q)}/", timeout=timeout, log_label="WAQI FEED")
    return normalize_feed_payload(payload), code


def fetch_search(keyword, timeout=6):
    q = normalize_query_text(keyword)
    if not q:
        return {"status": "ok", "data": []}, 200
    return waqi_get_json("/search/", params={"keyword": q}, timeout=timeout, log_label="WAQI SEARCH")


def fetch_map_bounds(lat1, lng1, lat2, lng2, timeout=8):
    bounds = f"{lat1},{lng1},{lat2},{lng2}"
    return waqi_get_json("/map/bounds/", params={"latlng": bounds}, timeout=timeout, log_label="WAQI MAP")


def parse_aqi_value(raw):
    txt = str(raw or "").strip()
    if txt in {"", "-", "--", "n/a", "N/A"}:
        return None
    try:
        return float(txt)
    except Exception:
        return None


def _extract_iaqi_value(iaqi, key):
    if not isinstance(iaqi, dict):
        return None
    node = iaqi.get(key)
    if isinstance(node, dict):
        return parse_aqi_value(node.get("v"))
    return parse_aqi_value(node)


def normalize_feed_payload(payload):
    """Normalize WAQI feed payload so valid live stations don't fail on missing top-level AQI."""
    if not isinstance(payload, dict):
        return payload
    if str(payload.get("status", "")).lower() != "ok":
        return payload
    data = payload.get("data")
    if not isinstance(data, dict):
        return payload

    aqi_val = parse_aqi_value(data.get("aqi"))
    if aqi_val is not None:
        return payload

    iaqi = data.get("iaqi")
    dominant = str(data.get("dominentpol", "")).strip().lower()
    priority = []
    if dominant:
        priority.append(dominant)
    priority.extend(["pm25", "pm10", "o3", "no2", "so2"])

    seen = set()
    fallback = None
    for key in priority:
        if key in seen:
            continue
        seen.add(key)
        val = _extract_iaqi_value(iaqi, key)
        if val is not None:
            fallback = val
            break

    if fallback is None:
        return payload

    out = dict(payload)
    patched_data = dict(data)
    patched_data["aqi"] = int(round(float(fallback)))
    out["data"] = patched_data
    return out


def is_valid_feed_payload(payload):
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status", "")).lower() != "ok":
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    if str(data.get("status", "")).lower() == "error":
        return False
    city = data.get("city")
    if not isinstance(city, dict):
        return False
    city_name = str(city.get("name", "")).strip()
    if not city_name:
        return False
    if re.fullmatch(r"@?\d+", city_name):
        return False
    if city_name.lower() in {"unknown station", "unknown", "n/a"}:
        return False
    if parse_aqi_value(data.get("aqi")) is None:
        return False
    return True


def normalize_station_text(text):
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    t = re.sub(r"[^a-z0-9,\s-]", "", t)
    return t.strip()


def station_candidate_score(item, normalized_query):
    station = item.get("station") or {}
    name = normalize_station_text(station.get("name", ""))
    if not name:
        return -10
    q = normalize_station_text(normalized_query)
    score = 0
    if name == q:
        score += 10
    if q and q in name:
        score += 6
    query_tokens = [t for t in q.split(" ") if t]
    if query_tokens:
        score += sum(1 for t in query_tokens if t in name)
    aqi_val = parse_aqi_value(item.get("aqi"))
    if aqi_val is not None:
        score += 1
    if item.get("uid") is not None:
        score += 2
    return score


def rank_search_candidates(search_payload, normalized_query):
    data = search_payload.get("data") if isinstance(search_payload, dict) else None
    if not isinstance(data, list):
        return []
    return sorted(
        data,
        key=lambda item: station_candidate_score(item, normalized_query),
        reverse=True,
    )


def with_resolved_meta(payload, input_query, normalized_query, source, matched_uid=None):
    out = dict(payload) if isinstance(payload, dict) else {"status": "error", "data": payload}
    out["resolved"] = {
        "input_query": str(input_query or "").strip(),
        "normalized_query": str(normalized_query or "").strip(),
        "source": source,
        "matched_uid": str(matched_uid) if matched_uid is not None else None,
    }
    return out


def _live_cache_key(raw_query):
    return normalize_query_text(raw_query).lower().strip()


def remember_live_cache(query_keys, payload):
    if not is_valid_feed_payload(payload):
        return
    snapshot = json.loads(json.dumps(payload))
    now = time.time()
    for raw in query_keys:
        key = _live_cache_key(raw)
        if key:
            LIVE_FEED_CACHE[key] = {"ts": now, "payload": snapshot}


def get_live_cache(query_keys):
    now = time.time()
    for raw in query_keys:
        key = _live_cache_key(raw)
        if not key:
            continue
        row = LIVE_FEED_CACHE.get(key)
        if not isinstance(row, dict):
            continue
        ts = float(row.get("ts", 0))
        payload = row.get("payload")
        if now - ts > LIVE_CACHE_TTL_SEC:
            LIVE_FEED_CACHE.pop(key, None)
            continue
        if is_valid_feed_payload(payload):
            return json.loads(json.dumps(payload))
    return None


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def compute_bounds_for_radius(lat, lng, radius_km):
    lat_delta = radius_km / 111.0
    cos_lat = max(abs(math.cos(math.radians(lat))), 0.2)
    lng_delta = radius_km / (111.0 * cos_lat)
    return lat - lat_delta, lng - lng_delta, lat + lat_delta, lng + lng_delta


def location_from_station_name(raw_name, fallback=""):
    raw = str(raw_name or "").strip()
    if not raw:
        return {"area": "", "city": str(fallback or "").strip(), "country": ""}
    parts = [re.sub(r"\s+", " ", p).strip() for p in raw.split(",") if p and p.strip()]
    if not parts:
        return {"area": "", "city": str(fallback or "").strip(), "country": ""}

    country = parts[-1]
    if len(country) <= 3 and country.upper() in COUNTRY_CODE_MAP:
        country = COUNTRY_CODE_MAP[country.upper()]
    elif len(country) <= 3 and country.lower() in COUNTRY_CODE_MAP:
        country = COUNTRY_CODE_MAP[country.lower()]

    middle = parts[1:-1] if len(parts) > 2 else parts[:-1]
    city = ""
    for token in reversed(middle):
        nt = token.lower().strip()
        if nt in STATE_HINTS:
            continue
        if len(nt) <= 2:
            continue
        city = token
        break
    if not city:
        city = parts[0] if len(parts) == 1 else parts[-2]

    area = ""
    first = parts[0].strip()
    if first and first.lower() != city.lower():
        nt = first.lower()
        if any(h in nt for h in AREA_HINTS) or any(ch.isdigit() for ch in nt):
            area = first
    return {"area": area, "city": city, "country": country}


def collect_live_area_rows(center_lat, center_lng, fallback_city="", radius_limit=32.0, max_rows=140):
    """Collect nearby live area stations and sort by AQI ascending (small to big)."""
    max_rows = max(20, min(int(max_rows or 140), 400))
    radius_limit = max(6.0, min(float(radius_limit or 32.0), 50.0))

    default_radii = [6.0, 12.0, 20.0, 30.0]
    radii = [r for r in default_radii if r <= radius_limit]
    if radius_limit not in radii:
        radii.append(radius_limit)

    rows_by_key = {}
    for radius in radii:
        lat1, lng1, lat2, lng2 = compute_bounds_for_radius(center_lat, center_lng, radius)
        map_payload, map_code = fetch_map_bounds(
            round(lat1, 6), round(lng1, 6), round(lat2, 6), round(lng2, 6), timeout=8
        )
        if map_code >= 500:
            continue
        stations = map_payload.get("data") if isinstance(map_payload, dict) else None
        if not isinstance(stations, list) or not stations:
            continue

        for st in stations:
            try:
                raw_aqi = parse_aqi_value(st.get("aqi"))
                if raw_aqi is None:
                    continue
                st_lat = float(st.get("lat"))
                st_lng = float(st.get("lon"))
                station_name = ((st.get("station") or {}).get("name") or "").strip()
                if not station_name:
                    continue
                uid = st.get("uid")
                loc = location_from_station_name(station_name, fallback=fallback_city)
                area = str(loc.get("area") or "").strip()
                city = str(loc.get("city") or fallback_city or "").strip()
                country = str(loc.get("country") or "").strip()
                distance = haversine_km(center_lat, center_lng, st_lat, st_lng)

                key = f"uid:{uid}" if uid is not None else f"name:{normalize_station_text(station_name)}"
                row = {
                    "uid": int(uid) if uid is not None else None,
                    "aqi": int(round(float(raw_aqi))),
                    "station_name": station_name,
                    "area": area,
                    "city": city,
                    "country": country,
                    "lat": round(st_lat, 6),
                    "lng": round(st_lng, 6),
                    "distance_km": round(distance, 3),
                }
                prev = rows_by_key.get(key)
                if prev is None or row["distance_km"] < prev["distance_km"]:
                    rows_by_key[key] = row
            except Exception:
                continue

    rows = list(rows_by_key.values())
    # Supplement map coverage with search localities (useful for sparse map-bounds regions).
    if len(rows) < max(15, int(max_rows * 0.65)):
        city_hint = location_from_station_name(fallback_city or "", fallback=fallback_city).get("city") or fallback_city
        search_payload, _ = fetch_search(city_hint)
        ranked = rank_search_candidates(search_payload, city_hint)
        uid_refetch_budget = 24
        for item in ranked:
            if len(rows_by_key) >= max_rows:
                break
            station = item.get("station") or {}
            station_name = str(station.get("name") or "").strip()
            if not station_name:
                continue
            uid = item.get("uid")
            key = f"uid:{uid}" if uid is not None else f"name:{normalize_station_text(station_name)}"
            if key in rows_by_key:
                continue

            aqi_val = parse_aqi_value(item.get("aqi"))
            st_geo = station.get("geo") if isinstance(station, dict) else None
            st_lat = st_lng = None
            if isinstance(st_geo, (list, tuple)) and len(st_geo) >= 2:
                try:
                    st_lat = float(st_geo[0])
                    st_lng = float(st_geo[1])
                except Exception:
                    st_lat = st_lng = None

            if (aqi_val is None or st_lat is None or st_lng is None) and uid is not None and uid_refetch_budget > 0:
                uid_refetch_budget -= 1
                feed_payload, _ = fetch_feed(f"@{uid}")
                if is_valid_feed_payload(feed_payload):
                    fdata = feed_payload.get("data") or {}
                    fcity = fdata.get("city") or {}
                    aqi_val = parse_aqi_value(fdata.get("aqi"))
                    fgeo = fcity.get("geo") if isinstance(fcity, dict) else None
                    if isinstance(fgeo, (list, tuple)) and len(fgeo) >= 2:
                        try:
                            st_lat = float(fgeo[0])
                            st_lng = float(fgeo[1])
                        except Exception:
                            pass
                    live_name = str(fcity.get("name") or "").strip()
                    if live_name:
                        station_name = live_name

            if aqi_val is None:
                continue
            if st_lat is None or st_lng is None:
                continue

            loc = location_from_station_name(station_name, fallback=fallback_city)
            area = str(loc.get("area") or "").strip()
            city = str(loc.get("city") or fallback_city or "").strip()
            country = str(loc.get("country") or "").strip()
            distance = haversine_km(center_lat, center_lng, st_lat, st_lng)
            rows_by_key[key] = {
                "uid": int(uid) if uid is not None else None,
                "aqi": int(round(float(aqi_val))),
                "station_name": station_name,
                "area": area,
                "city": city,
                "country": country,
                "lat": round(st_lat, 6),
                "lng": round(st_lng, 6),
                "distance_km": round(distance, 3),
            }

    rows = list(rows_by_key.values())
    rows.sort(key=lambda r: (r.get("aqi", 9999), r.get("distance_km", 9999), normalize_station_text(r.get("station_name", ""))))
    return rows[:max_rows]

def get_time_phase_from_iso(time_iso):
    """Map local timestamp to day/evening/night bands."""
    dt = None
    txt = str(time_iso or "").strip()
    if txt:
        try:
            dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        except Exception:
            dt = None
    if dt is None:
        dt = datetime.now()

    h = dt.hour
    if 6 <= h <= 16:
        return "day"
    if 17 <= h <= 19:
        return "evening"
    return "night"

def build_nlp_advice(city, country, aqi, dominant, temp, humidity, wind, time_iso):
    cat = get_category(int(max(0, aqi)))
    risk = cat["level"]
    phase = get_time_phase_from_iso(time_iso)
    dominant_key = str(dominant or "pm25").lower().strip()

    pollutant_hint = {
        "pm25": "Fine particles are elevated, so lungs can be irritated quickly.",
        "pm10": "Dust-like particles are high; prolonged exposure may trigger coughing.",
        "o3": "Ground-level ozone is elevated and can cause throat irritation outdoors.",
        "no2": "Nitrogen dioxide is elevated, especially risky near traffic corridors.",
        "so2": "Sulfur dioxide is elevated and may aggravate asthma symptoms.",
        "co": "Carbon monoxide exposure risk is higher; ensure good indoor ventilation.",
    }
    risk_precautions = {
        "Good": [
            "Normal outdoor activity is generally safe.",
            "Keep hydration steady during longer outdoor sessions.",
            "Sensitive individuals should still monitor any unusual symptoms.",
        ],
        "Moderate": [
            "Sensitive groups should reduce long, intense outdoor exercise.",
            "Prefer parks or low-traffic routes instead of congested roads.",
            "If irritation starts, move indoors and rest.",
        ],
        "Poor": [
            "Limit prolonged outdoor exposure, especially for children and elders.",
            "Avoid outdoor workouts near traffic-heavy areas.",
            "Use a well-fitted mask if you must stay outside for long periods.",
        ],
        "Unhealthy": [
            "Reduce outdoor time and postpone non-essential outdoor activities.",
            "Keep windows closed during peak traffic and dusty periods.",
            "Use an N95/FFP2-type mask when stepping outside.",
        ],
        "Severe": [
            "Avoid outdoor exertion and keep activities indoors.",
            "Run an air purifier in occupied rooms if available.",
            "Limit children, elderly, and respiratory patients to indoor spaces.",
        ],
        "Hazardous": [
            "Stay indoors as much as possible and avoid all outdoor exertion.",
            "Seal indoor air leaks where possible and run air cleaning continuously.",
            "Go outside only if essential, with a high-filtration mask.",
        ],
    }
    risk_measures = {
        "Good": [
            "Track AQI every few hours before planning long outdoor sessions.",
            "Maintain indoor ventilation when outdoor air remains stable.",
            "Carry water and avoid smoke exposure sources.",
        ],
        "Moderate": [
            "Schedule walks in cleaner periods and avoid rush-hour traffic zones.",
            "Keep quick-relief inhalers accessible for sensitive users.",
            "Use indoor plants and regular cleaning to reduce indoor dust load.",
        ],
        "Poor": [
            "Shift exercise indoors or to low-pollution time windows.",
            "Use masks during commute and near construction areas.",
            "Use recirculation mode in vehicles during congestion.",
        ],
        "Unhealthy": [
            "Postpone strenuous activities until AQI improves.",
            "Ventilate home only when outdoor levels temporarily drop.",
            "Use saline rinses/steam for irritation relief where medically appropriate.",
        ],
        "Severe": [
            "Create a clean-air room with purifier and closed windows.",
            "Restrict exposure for high-risk groups and monitor symptoms closely.",
            "Consult a clinician if breathlessness, wheeze, or chest pain appears.",
        ],
        "Hazardous": [
            "Activate emergency indoor air-protection routine immediately.",
            "Avoid opening windows and doors except when necessary.",
            "Seek medical help promptly if severe respiratory discomfort occurs.",
        ],
    }
    phase_tip = {
        "day": "Midday sun can increase secondary pollutants in some regions.",
        "evening": "Evening traffic can increase roadside exposure.",
        "night": "Night-time inversion may trap pollutants near ground level.",
    }
    climate_note = ""
    if humidity >= 75:
        climate_note = " High humidity can make breathing feel heavier for sensitive users."
    elif wind >= 8:
        climate_note = " Strong wind may disperse some pollutants but can also carry dust locally."
    elif temp >= 34:
        climate_note = " Hot weather can increase discomfort during exposure."

    if aqi <= 100:
        best_time_outdoor = "Early morning or post-rain periods are generally preferred."
    elif aqi <= 200:
        best_time_outdoor = "Keep outdoor tasks short and choose lower-traffic hours."
    else:
        best_time_outdoor = "Avoid outdoor activity until AQI improves."

    if phase == "evening":
        best_time_outdoor = "Prefer short outdoor tasks before heavy evening traffic."
    elif phase == "night" and aqi > 150:
        best_time_outdoor = "Delay outdoor exposure until cleaner daytime windows."

    if aqi <= 100:
        mask_recommendation = "Mask is optional for most people; sensitive users may prefer light protection."
    elif aqi <= 200:
        mask_recommendation = "Use a well-fitted N95/FFP2 mask for longer outdoor exposure."
    else:
        mask_recommendation = "Use a high-filtration N95/FFP2 mask for any essential outdoor movement."

    sensitive_groups_note = (
        "Children, elderly adults, pregnant women, and people with asthma/COPD or heart disease "
        "should follow stricter exposure limits."
    )

    city_label = str(city or "Unknown").strip() or "Unknown"
    country_label = str(country or "").strip()
    city_full = f"{city_label}, {country_label}" if country_label else city_label
    summary = (
        f"In {city_full}, AQI is {int(round(aqi))} ({risk}). "
        f"{pollutant_hint.get(dominant_key, 'Air pollution levels require cautious exposure planning.')} "
        f"{phase_tip[phase]}{climate_note}"
    )

    return {
        "city": city_label,
        "aqi": round(float(aqi), 1),
        "risk_level": risk,
        "summary": summary,
        "precautions": risk_precautions[risk][:3],
        "measures": risk_measures[risk][:3],
        "best_time_outdoor": best_time_outdoor,
        "mask_recommendation": mask_recommendation,
        "sensitive_groups_note": sensitive_groups_note,
    }

# ── Initialize data & ML models ────────────────────────────────
def initialize():
    global df, model, scaler, encoders
    print("="*60)
    print("  Initializing Air Quality Dashboard")
    print("="*60)

    def first_existing_path(candidates):
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return ""

    base_dir = os.path.dirname(__file__)
    csv_path = first_existing_path([
        os.path.join(base_dir, "globalAirQuality.csv"),
        os.path.join(base_dir, "Sample_Dataset", "globalAirQuality.csv"),
    ])
    if csv_path:
        try:
            df = pd.read_csv(csv_path)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            print(f"✓ Dataset: {len(df):,} records | {df['city'].nunique()} cities")
            print(f"✓ Dataset path: {csv_path}")
        except Exception as e:
            print(f"✗ Dataset error: {e}"); df = None
    else:
        print("✗ globalAirQuality.csv not found in project root or Sample_Dataset/")

    try:
        import joblib
        for fname, var_name in [
            ("aqi_model_random_forest.pkl", "model"),
            ("aqi_scaler.pkl",              "scaler"),
            ("aqi_encoders.pkl",            "encoders"),
        ]:
            path = first_existing_path([
                os.path.join(base_dir, fname),
                os.path.join(base_dir, "ML-Model", fname),
            ])
            if path:
                globals()[var_name] = joblib.load(path)
                print(f"✓ Loaded: {fname} ({path})")
            else:
                print(f"⚠ Missing: {fname} (checked root + ML-Model/)")
    except Exception as e:
        print(f"⚠ ML models unavailable: {e}")

    print("="*60)
    print(f"✓ WAQI base: {WAQI_BASE_URL}")
    print(f"✓ WAQI token source: {WAQI_TOKEN_SOURCE}")
    print("✓ Dashboard ready!" if df is not None else "⚠ Demo mode — no CSV found")
    print("="*60)

# ═══════════════════════════════════════════════════════════════
#  ROUTES — Pages
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    # pass timestamps for static cache busting and runtime build verification
    now_ts = int(time.time())
    resp = make_response(render_template("index.html", ts=now_ts, build_ts=APP_BUILD_TS))
    # explicit no-store on home HTML to avoid stale dashboard shell
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/predict")
def predict_page():
    return render_template("predict.html", ts=int(time.time()), build_ts=APP_BUILD_TS)

@app.route("/analytics")
def analytics_page():
    return render_template("analytics.html", ts=int(time.time()), build_ts=APP_BUILD_TS)

@app.after_request
def disable_cache(resp):
    """Avoid stale CSS/JS/HTML in browser cache during active development."""
    if request.path.startswith("/static/") or resp.mimetype in ("text/html", "text/css", "application/javascript", "text/javascript"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

# ═══════════════════════════════════════════════════════════════
#  ROUTES — Live WAQI API Proxy
#  Correct format: https://api.waqi.info/feed/{city}/?token={TOKEN}
# ═══════════════════════════════════════════════════════════════

def resolve_best_live_payload(raw_query):
    input_query = normalize_query_text(raw_query)
    normalized_query, _ = normalize_live_query(input_query)
    candidates = []
    for q in [normalized_query, input_query]:
        if q and q not in candidates:
            candidates.append(q)

    best_error_payload = {"status": "error", "data": f"Unknown station for query '{input_query}'"}
    best_status = 404

    def error_result(source):
        payload = best_error_payload
        status = best_status if isinstance(best_status, int) and best_status >= 400 else 404
        if not isinstance(payload, dict) or str(payload.get("status", "")).lower() != "error":
            payload = {"status": "error", "data": f"No valid live station found for '{input_query}'"}
        return with_resolved_meta(payload, input_query, normalized_query, source), status

    def capture_error(payload, code):
        nonlocal best_error_payload, best_status
        if isinstance(payload, dict) and str(payload.get("status", "")).lower() == "error":
            best_error_payload = payload
            best_status = code

    for candidate in candidates:
        payload, code = fetch_feed(candidate, timeout=6)
        if is_valid_feed_payload(payload):
            source = "alias" if candidate != input_query else "direct"
            return with_resolved_meta(payload, input_query, normalized_query, source), 200
        capture_error(payload, code)

    if normalized_query.startswith("@") and re.fullmatch(r"@\d+", normalized_query):
        return error_result("direct")

    search_payload, search_code = fetch_search(normalized_query or input_query, timeout=5)
    if not isinstance(search_payload, dict):
        return error_result("direct")
    if str(search_payload.get("status", "")).lower() != "ok":
        capture_error(search_payload, search_code)
        return error_result("search_name")
    if not isinstance(search_payload.get("data"), list):
        return error_result("search_name")

    ranked = rank_search_candidates(search_payload, normalized_query or input_query)
    if not ranked:
        return error_result("search_name")
    for item in ranked[:10]:
        uid = item.get("uid")
        station = item.get("station") or {}
        station_name = normalize_query_text(station.get("name", ""))
        if uid is not None:
            payload, code = fetch_feed(f"@{uid}", timeout=6)
            if is_valid_feed_payload(payload):
                return with_resolved_meta(payload, input_query, normalized_query, "search_uid", matched_uid=uid), 200
            capture_error(payload, code)
        if station_name:
            payload, code = fetch_feed(station_name, timeout=6)
            if is_valid_feed_payload(payload):
                return with_resolved_meta(payload, input_query, normalized_query, "search_name", matched_uid=uid), 200
            capture_error(payload, code)

    return error_result("search_uid")


@app.route("/api/live/<path:city>")
def live_aqi(city):
    """Resolve live AQI from WAQI using direct query + alias + search fallback."""
    if not WAQI_TOKEN:
        return jsonify({"status": "error", "data": "WAQI token not configured on server"}), 503
    input_query = normalize_query_text(city)
    normalized_query, _ = normalize_live_query(input_query)
    payload, code = resolve_best_live_payload(city)
    if is_valid_feed_payload(payload):
        remember_live_cache([input_query, normalized_query], payload)
        return jsonify(payload), code

    cached = get_live_cache([input_query, normalized_query])
    if cached:
        cached = with_resolved_meta(cached, input_query, normalized_query, "cache")
        return jsonify(cached), 200

    return jsonify(payload), code


@app.route("/api/live/geo/<float(signed=True):lat>/<float(signed=True):lon>")
def live_aqi_geo(lat, lon):
    """Proxy WAQI geo lookup: /feed/geo:{lat};{lon}/?token=..."""
    if not WAQI_TOKEN:
        return jsonify({"status": "error", "data": "WAQI token not configured on server"}), 503
    payload, code = fetch_feed(f"geo:{lat};{lon}")
    if is_valid_feed_payload(payload):
        payload = with_resolved_meta(payload, f"geo:{lat};{lon}", f"geo:{lat};{lon}", "geo")
    elif isinstance(payload, dict) and str(payload.get("status", "")).lower() == "ok":
        payload = {"status": "error", "data": "No valid nearby live station found via geo feed"}
        code = 404
    return jsonify(payload), code

@app.route("/api/live/search/<path:keyword>")
def live_search(keyword):
    """Search cities via WAQI"""
    if not WAQI_TOKEN:
        return jsonify({"status": "error", "data": "WAQI token not configured on server"}), 503
    payload, code = fetch_search(keyword)
    return jsonify(payload), code


@app.route("/api/live/areas/<path:city>")
def live_area_list(city):
    """Area-wise live AQI list around the selected city (AQI low -> high)."""
    if not WAQI_TOKEN:
        return jsonify({"status": "error", "data": "WAQI token not configured on server"}), 503

    limit = int(safe_float(request.args.get("limit"), 140))
    limit = max(20, min(limit, 400))
    radius_km = safe_float(request.args.get("radius_km"), 32.0)

    payload, code = resolve_best_live_payload(city)
    if not is_valid_feed_payload(payload):
        return jsonify(payload), code

    data = payload.get("data") or {}
    city_meta = data.get("city") or {}
    geo = city_meta.get("geo") if isinstance(city_meta, dict) else None
    try:
        center_lat = float(geo[0])
        center_lng = float(geo[1])
    except Exception:
        return jsonify({
            "status": "error",
            "data": "City center coordinates unavailable for area lookup",
            "resolved": payload.get("resolved"),
        }), 422

    rows = collect_live_area_rows(
        center_lat=center_lat,
        center_lng=center_lng,
        fallback_city=normalize_query_text(city),
        radius_limit=radius_km,
        max_rows=limit,
    )

    # Ensure at least the resolved city station is present when map coverage is sparse.
    if not rows:
        loc = location_from_station_name(str(city_meta.get("name") or city), fallback=str(city))
        base_aqi = parse_aqi_value(data.get("aqi"))
        if base_aqi is not None:
            rows = [{
                "uid": data.get("idx"),
                "aqi": int(round(float(base_aqi))),
                "station_name": str(city_meta.get("name") or city).strip(),
                "area": str(loc.get("area") or "").strip(),
                "city": str(loc.get("city") or city).strip(),
                "country": str(loc.get("country") or "").strip(),
                "lat": round(center_lat, 6),
                "lng": round(center_lng, 6),
                "distance_km": 0.0,
            }]

    out = {
        "status": "ok",
        "city": {
            "name": str(city_meta.get("name") or city).strip(),
            "lat": round(center_lat, 6),
            "lng": round(center_lng, 6),
        },
        "sort": "aqi_asc",
        "count": len(rows),
        "areas": rows,
        "resolved": payload.get("resolved"),
    }
    return jsonify(out), 200


@app.route("/api/live/nearby")
def live_nearby():
    """Resolve nearest live station for precise geolocation-based AQI."""
    if not WAQI_TOKEN:
        return jsonify({"status": "error", "data": "WAQI token not configured on server"}), 503

    try:
        lat = float(request.args.get("lat", ""))
        lng = float(request.args.get("lng", ""))
    except Exception:
        return jsonify({"status": "error", "data": "Invalid lat/lng"}), 400

    radius_limit = safe_float(request.args.get("radius_km"), 20.0)
    if radius_limit <= 0:
        radius_limit = 20.0
    radius_limit = min(radius_limit, 30.0)

    default_radii = [4.0, 8.0, 12.0, 20.0]
    radii = [r for r in default_radii if r <= radius_limit]
    if radius_limit not in radii:
        radii.append(radius_limit)

    best_station = None
    for radius in radii:
        lat1, lng1, lat2, lng2 = compute_bounds_for_radius(lat, lng, radius)
        map_payload, map_code = fetch_map_bounds(
            round(lat1, 6), round(lng1, 6), round(lat2, 6), round(lng2, 6), timeout=8
        )
        stations = map_payload.get("data") if isinstance(map_payload, dict) else None
        if map_code >= 500:
            continue
        if not isinstance(stations, list) or not stations:
            continue

        nearest = None
        for st in stations:
            try:
                st_lat = float(st.get("lat"))
                st_lng = float(st.get("lon"))
                uid = st.get("uid")
                if uid is None:
                    continue
                dist = haversine_km(lat, lng, st_lat, st_lng)
                if nearest is None or dist < nearest["distance_km"]:
                    loc = location_from_station_name((st.get("station") or {}).get("name", ""))
                    nearest = {
                        "uid": int(uid),
                        "station_name": ((st.get("station") or {}).get("name") or "").strip(),
                        "lat": round(st_lat, 6),
                        "lng": round(st_lng, 6),
                        "distance_km": round(dist, 3),
                        "location": loc,
                    }
            except Exception:
                continue

        if nearest is None:
            continue

        feed_payload, feed_code = fetch_feed(f"@{nearest['uid']}")
        if is_valid_feed_payload(feed_payload):
            out = with_resolved_meta(
                feed_payload,
                f"{lat},{lng}",
                f"{lat},{lng}",
                "nearby_uid",
                matched_uid=nearest["uid"],
            )
            out["nearest"] = nearest
            print(
                f"✓ Nearby station resolved via uid={nearest['uid']} "
                f"distance={nearest['distance_km']:.3f} km"
            )
            return jsonify(out), 200
        best_station = nearest

    err = {
        "status": "error",
        "data": "No nearby live station found for this location",
    }
    if best_station:
        err["nearest"] = best_station
    return jsonify(err), 404

@app.route("/api/live-map-bounds")
def live_map_bounds():
    """Proxy WAQI map bounds stations for area-level AQI markers."""
    if not WAQI_TOKEN:
        return jsonify({"status":"error","data":"WAQI token not configured on server"}), 503
    try:
        lat1 = float(request.args.get("lat1"))
        lng1 = float(request.args.get("lng1"))
        lat2 = float(request.args.get("lat2"))
        lng2 = float(request.args.get("lng2"))
    except Exception:
        return jsonify({"status":"error","data":"Invalid map bounds"}), 400

    payload, code = fetch_map_bounds(lat1, lng1, lat2, lng2, timeout=8)
    return jsonify(payload), code

# ═══════════════════════════════════════════════════════════════
#  ROUTES — Historical / CSV-backed APIs
# ═══════════════════════════════════════════════════════════════

@app.route("/api/status")
def api_status():
    return jsonify({
        "status":       "online",
        "data_records": len(df) if df is not None else 0,
        "cities":       int(df["city"].nunique()) if df is not None else 0,
        "model_loaded": model is not None,
        "waqi_base":    WAQI_BASE_URL,
        "token_source": WAQI_TOKEN_SOURCE,
        "token_configured": bool(WAQI_TOKEN),
        "build_ts":     APP_BUILD_TS,
        "timestamp":    datetime.now().isoformat(),
    })

def build_current_aqi_from_live_payload(live_payload, requested_city=""):
    """Map a WAQI feed payload into the /api/current-aqi response shape."""
    if not is_valid_feed_payload(live_payload):
        return None

    data = live_payload.get("data") if isinstance(live_payload, dict) else None
    if not isinstance(data, dict):
        return None

    aqi_val = parse_aqi_value(data.get("aqi"))
    if aqi_val is None:
        return None
    cat = get_category(int(max(0, round(float(aqi_val)))))

    city_meta = data.get("city") if isinstance(data.get("city"), dict) else {}
    station_name = str(city_meta.get("name") or requested_city).strip()
    loc = location_from_station_name(station_name, fallback=requested_city)
    city_name = str(loc.get("city") or requested_city or station_name).strip() or "Unknown"
    country_name = str(loc.get("country") or "").strip()

    geo = city_meta.get("geo") if isinstance(city_meta, dict) else None
    lat = lng = None
    if isinstance(geo, (list, tuple)) and len(geo) >= 2:
        try:
            lat = float(geo[0])
            lng = float(geo[1])
        except Exception:
            lat = lng = None

    iaqi = data.get("iaqi") if isinstance(data.get("iaqi"), dict) else {}
    def iaqi_value(key):
        node = iaqi.get(key)
        if isinstance(node, dict):
            return parse_aqi_value(node.get("v"))
        return parse_aqi_value(node)

    time_meta = data.get("time") if isinstance(data.get("time"), dict) else {}
    timestamp = str(time_meta.get("s") or time_meta.get("iso") or "").strip()
    if not timestamp:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    return {
        "aqi": float(aqi_val),
        "category": cat["level"],
        "color": cat["color"],
        "bg": cat["bg"],
        "description": cat["text"],
        "city": city_name,
        "country": country_name,
        "latitude": lat,
        "longitude": lng,
        "timestamp": timestamp,
        "pollutants": {
            "pm25": iaqi_value("pm25"),
            "pm10": iaqi_value("pm10"),
            "no2": iaqi_value("no2"),
            "so2": iaqi_value("so2"),
            "o3": iaqi_value("o3"),
            "co": iaqi_value("co"),
        },
        "weather": {
            "temperature": iaqi_value("t"),
            "humidity": iaqi_value("h"),
            "wind_speed": iaqi_value("w"),
        },
    }

@app.route("/api/current-aqi")
def current_aqi():
    try:
        def norm_city(raw):
            txt = str(raw or "").lower().strip()
            txt = txt.replace("@", "").split(",")[0].strip()
            txt = re.sub(r"[^a-z0-9\s-]", " ", txt)
            txt = re.sub(r"\s+", " ", txt).strip()
            aliases = {
                "bangalore": "bengaluru",
                "new york city": "new york",
                "nyc": "new york",
                "delhi ncr": "delhi",
            }
            return aliases.get(txt, txt)

        raw_city = normalize_query_text(request.args.get("city"))
        city_q = norm_city(raw_city)

        if df is None:
            live_query = raw_city or "delhi"
            live_payload, live_code = resolve_best_live_payload(live_query)
            live_row = build_current_aqi_from_live_payload(live_payload, requested_city=live_query)
            if live_row:
                return jsonify(live_row)
            msg = live_payload.get("data") if isinstance(live_payload, dict) else "Live fallback failed"
            return jsonify({"error": f"No data — CSV missing and live fallback failed: {msg}"}), live_code or 404

        city_series = (
            df["city"]
            .astype(str)
            .str.lower()
            .str.replace(r"[^a-z0-9\s-]", " ", regex=True)
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
        )

        latest = None
        if city_q:
            dcity = df[city_series == city_q]
            if dcity.empty:
                dcity = df[city_series.str.contains(city_q, regex=False, na=False)]
            if dcity.empty and raw_city:
                live_payload, _ = resolve_best_live_payload(raw_city)
                live_row = build_current_aqi_from_live_payload(live_payload, requested_city=raw_city)
                if live_row:
                    return jsonify(live_row)
            if not dcity.empty:
                latest = dcity.sort_values("timestamp").iloc[-1]
            # If city not found, fall back to latest global record instead of error
            # This ensures we always return SOME data rather than failing

        if latest is None:
            latest = df.sort_values("timestamp").iloc[-1]

        aqi_val = float(latest["aqi"])
        cat = get_category(int(aqi_val))
        return jsonify({
            "aqi": aqi_val, "category": cat["level"],
            "color": cat["color"], "bg": cat["bg"],
            "description": cat["text"],
            "city": latest["city"], "country": latest["country"],
            "latitude": float(latest["latitude"]) if "latitude" in latest else None,
            "longitude": float(latest["longitude"]) if "longitude" in latest else None,
            "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M"),
            "pollutants": {k: float(latest[k]) for k in ["pm25","pm10","no2","so2","o3","co"]},
            "weather":    {k: float(latest[k]) for k in ["temperature","humidity","wind_speed"]},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/nlp/advice")
def nlp_advice():
    """Deterministic NLP-style AQI guidance."""
    try:
        city = request.args.get("city", "")
        country = request.args.get("country", "")
        aqi = safe_float(request.args.get("aqi"), 0)
        dominant = request.args.get("dominant", "pm25")
        temp = safe_float(request.args.get("temp"), 0)
        humidity = safe_float(request.args.get("humidity"), 0)
        wind = safe_float(request.args.get("wind"), 0)
        time_iso = request.args.get("time_iso", "")

        advice = build_nlp_advice(
            city=city,
            country=country,
            aqi=aqi,
            dominant=dominant,
            temp=temp,
            humidity=humidity,
            wind=wind,
            time_iso=time_iso,
        )
        return jsonify(advice)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/historical")
def historical():
    if df is None:
        return jsonify({"error":"No data"}), 404
    try:
        city = request.args.get("city")
        hours = int(request.args.get("hours", 24))
        data = df[df["city"] == city].tail(hours) if city else df.tail(hours)
        return jsonify({
            "timestamps": data["timestamp"].dt.strftime("%H:%M").tolist(),
            "aqi":  data["aqi"].tolist(),
            "pm25": data["pm25"].tolist(),
            "pm10": data["pm10"].tolist(),
            "no2":  data["no2"].tolist(),
            "so2":  data["so2"].tolist(),
            "o3":   data["o3"].tolist(),
            "co":   data["co"].tolist(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/statistics")
def statistics():
    if df is None:
        return jsonify({"error":"No data"}), 404
    try:
        return jsonify({
            "total_readings":  int(len(df)),
            "avg_aqi":         round(float(df["aqi"].mean()), 1),
            "max_aqi":         int(df["aqi"].max()),
            "min_aqi":         int(df["aqi"].min()),
            "cities_monitored":int(df["city"].nunique()),
            "avg_pm25":        round(float(df["pm25"].mean()), 1),
            "avg_pm10":        round(float(df["pm10"].mean()), 1),
            "avg_temperature": round(float(df["temperature"].mean()), 1),
            "avg_humidity":    round(float(df["humidity"].mean()), 1),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/city-locations")
def city_locations():
    if df is None:
        return jsonify({"error":"No data"}), 404
    try:
        latest = df.sort_values("timestamp").groupby("city").last().reset_index()
        locations = []
        for _, row in latest.iterrows():
            cat = get_category(int(row["aqi"]))
            locations.append({
                "city": row["city"], "country": row["country"],
                "lat":  float(row["latitude"]),  "lng": float(row["longitude"]),
                "aqi":  float(row["aqi"]),
                "color": cat["color"], "category": cat["level"],
            })
        return jsonify({"locations": locations})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/heatmap")
def heatmap():
    if df is None:
        return jsonify({"error":"No data"}), 404
    try:
        tmp = df.copy()
        tmp["hour"] = tmp["timestamp"].dt.hour
        tmp["day"]  = tmp["timestamp"].dt.day_name()
        days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        grp  = tmp.groupby(["day","hour"])["aqi"].mean().reset_index()
        matrix = []
        for d in days:
            row = []
            for h in range(24):
                v = grp[(grp["day"]==d)&(grp["hour"]==h)]
                row.append(round(float(v["aqi"].values[0]),1) if len(v)>0 else 0)
            matrix.append(row)
        return jsonify({"days": days, "hours": list(range(24)), "data": matrix})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/city-ranking")
def city_ranking():
    if df is None:
        return jsonify({"error":"No data"}), 404
    try:
        latest = df.sort_values("timestamp").groupby("city").last().reset_index()
        latest = latest.sort_values("aqi", ascending=False)
        rows = []
        for _, r in latest.iterrows():
            cat = get_category(int(r["aqi"]))
            rows.append({
                "city": r["city"], "country": r["country"],
                "aqi": float(r["aqi"]),
                "level": cat["level"], "color": cat["color"],
                "pm25": round(float(r["pm25"]),1),
                "timestamp": r["timestamp"].strftime("%Y-%m-%d %H:%M"),
            })
        return jsonify({"cities": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/export")
def export_data():
    if df is None:
        return jsonify({"error":"No data"}), 404
    return df.to_csv(index=False), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=aqi_export.csv",
    }

# ═══════════════════════════════════════════════════════════════
#  ROUTES — ML Prediction
# ═══════════════════════════════════════════════════════════════

@app.route("/api/predict", methods=["POST"])
def predict_aqi():
    try:
        data = request.json
        pm25        = float(data.get("pm25", 0))
        pm10        = float(data.get("pm10", 0))
        no2         = float(data.get("no2",  0))
        so2         = float(data.get("so2",  0))
        o3          = float(data.get("o3",   0))
        co          = float(data.get("co",   0))
        temperature = float(data.get("temperature", 25))
        humidity    = float(data.get("humidity",    50))
        wind_speed  = float(data.get("wind_speed",   5))

        # EPA PM2.5 AQI formula
        if pm25 <= 12.0:
            predicted = (50/12.0) * pm25
        elif pm25 <= 35.4:
            predicted = ((100-51)/(35.4-12.1)) * (pm25-12.1) + 51
        elif pm25 <= 55.4:
            predicted = ((150-101)/(55.4-35.5)) * (pm25-35.5) + 101
        elif pm25 <= 150.4:
            predicted = ((200-151)/(150.4-55.5)) * (pm25-55.5) + 151
        elif pm25 <= 250.4:
            predicted = ((300-201)/(250.4-150.5)) * (pm25-150.5) + 201
        else:
            predicted = ((400-301)/(350.4-250.5)) * (pm25-250.5) + 301

        # If ML model is available, blend prediction
        ml_predicted = None
        if model is not None and scaler is not None:
            try:
                now = datetime.now()
                features = np.array([[
                    pm25, pm10, no2, so2, o3, co,
                    temperature, humidity, wind_speed,
                    now.hour, now.day, now.month, now.weekday(),
                    1 if now.weekday() >= 5 else 0,
                    pm25/pm10 if pm10 > 0 else 0,
                    pm25+pm10+no2+so2+o3+co,
                    temperature*humidity/100,
                    0, 0, 0, 0  # encoded city/country placeholders
                ]])
                scaled  = scaler.transform(features)
                ml_predicted = float(model.predict(scaled)[0])
                # Blend: 60% EPA formula, 40% ML model
                predicted = 0.6*predicted + 0.4*ml_predicted
            except Exception:
                pass  # fall back to EPA formula

        cat = get_category(int(predicted))

        total = pm25 + pm10 + no2 + so2 + o3 + (co*10)
        contributions = {k: round(v/total*100, 1) if total > 0 else 0
            for k,v in [("pm25",pm25),("pm10",pm10),("no2",no2),
                         ("so2",so2),("o3",o3),("co",co*10)]}

        tips_map = {
            "Good":     ["Air quality is excellent! 🌿","Great day for outdoor activities.","No health concerns for anyone."],
            "Moderate": ["Air quality is acceptable.","Unusually sensitive people should limit prolonged outdoor exertion.","Generally safe for most people."],
            "Poor":     ["Sensitive groups should reduce outdoor activity.","People with respiratory issues be cautious.","General public less likely affected."],
            "Unhealthy":["Everyone should reduce prolonged outdoor exertion.","Sensitive groups should avoid outdoor activities.","Consider wearing a mask outside."],
            "Severe":   ["Everyone should avoid outdoor activities.","Stay indoors with windows closed.","Use air purifiers if available."],
            "Hazardous":["Stay indoors! ⚠️","Avoid all outdoor activities immediately.","Use air purifiers and N95 masks if going outside."],
        }

        return jsonify({
            "success": True,
            "predicted_aqi": round(predicted, 1),
            "ml_predicted":  round(ml_predicted, 1) if ml_predicted else None,
            "category":      cat["level"],
            "color":         cat["color"],
            "bg":            cat["bg"],
            "description":   cat["text"],
            "contributions": contributions,
            "health_tips":   tips_map.get(cat["level"], []),
            "method":        "EPA+ML Blend" if ml_predicted else "EPA Formula",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

# ── Error handlers ──────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error":"Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error":"Internal server error"}), 500

# ── Boot ────────────────────────────────────────────────────────
initialize()

if __name__ == "__main__":
    print(f"\n🌍 Air Quality Dashboard")
    print(f"📊 http://localhost:{FLASK_PORT}")
    print(f"🤖 http://localhost:{FLASK_PORT}/predict")
    print(f"📈 http://localhost:{FLASK_PORT}/analytics")
    print(f"🧩 Build TS: {APP_BUILD_TS}")
    print(f"🔗 WAQI API: {WAQI_BASE_URL}  ← correct https://\n")
    app.run(debug=FLASK_DEBUG, use_reloader=False, host="0.0.0.0", port=FLASK_PORT)
