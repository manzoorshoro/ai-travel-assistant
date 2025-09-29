import streamlit as st
import requests
import pandas as pd
from urllib.parse import quote_plus
from streamlit_js_eval import get_geolocation

# ---------- Page ----------
st.set_page_config(page_title="AI Travel Assistant", layout="wide")
st.title("🌍 AI Travel Assistant — vCloud")
st.caption("Auto-locate → show **weather**, **nearby restaurants**, **attractions**, and **local news**.")

# ---------- Helpers ----------
def geocode_city(city: str):
    """Open-Meteo geocoder → (lat, lon, name, admin1, country, timezone)"""
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={quote_plus(city)}&count=1&language=en&format=json"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("results"):
        return None
    r0 = data["results"][0]
    return {
        "lat": float(r0["latitude"]),
        "lon": float(r0["longitude"]),
        "name": r0.get("name"),
        "admin1": r0.get("admin1"),
        "country": r0.get("country"),
        "timezone": r0.get("timezone"),
        "source": "city-search",
    }

def reverse_geocode(lat: float, lon: float):
    """First try OSM Nominatim (English), fall back to Open-Meteo reverse."""
    # OSM Nominatim
    try:
        nomi = (
            "https://nominatim.openstreetmap.org/reverse"
            f"?format=json&lat={lat}&lon={lon}&zoom=10&addressdetails=1"
        )
        r = requests.get(
            nomi, timeout=12,
            headers={"User-Agent": "AI-Travel-Assistant/0.4.x", "Accept-Language": "en"},
        )
        r.raise_for_status()
        j = r.json() if isinstance(r.json(), dict) else {}
        addr = j.get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") \
               or addr.get("municipality") or addr.get("county")
        admin1 = addr.get("state") or addr.get("region")
        country = addr.get("country")
        if city or admin1 or country:
            return {
                "name": city,
                "admin1": admin1,
                "country": country,
                "timezone": None,
                "source": "nominatim",
            }
    except Exception:
        pass

    # Open-Meteo reverse
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/reverse?latitude={lat}&longitude={lon}&language=en&format=json"
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        j = r.json()
        if j.get("results"):
            r0 = j["results"][0]
            return {
                "name": r0.get("name"),
                "admin1": r0.get("admin1"),
                "country": r0.get("country"),
                "timezone": r0.get("timezone"),
                "source": "open-meteo-rev",
            }
    except Exception:
        pass

    return {"name": None, "admin1": None, "country": None, "timezone": None, "source": "unknown"}

def get_weather(lat: float, lon: float, timezone: str | None):
    tz = timezone or "auto"
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,apparent_temperature,wind_speed_10m,relative_humidity_2m"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&forecast_days=3"
        f"&timezone={quote_plus(tz)}"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

# ---------- Sidebar Controls ----------
st.sidebar.header("Controls")
force_city = st.sidebar.text_input("Search city (e.g., Dubai, Karachi, London)")
use_city = st.sidebar.button("Use searched city")

st.sidebar.subheader("📍 Location options")
get_loc = st.sidebar.button("Get my location")

# Ensure session key exists
if "location" not in st.session_state:
    st.session_state["location"] = None
if "loc_source" not in st.session_state:
    st.session_state["loc_source"] = None

# ---------- 1) City search ----------
if use_city and force_city.strip():
    city = force_city.strip()
    try:
        meta = geocode_city(city)
        if meta:
            st.session_state["location"] = {
                "city": meta["name"],
                "region": meta.get("admin1"),
                "country": meta.get("country"),
                "coords": (meta["lat"], meta["lon"]),
                "timezone": meta.get("timezone"),
            }
            st.session_state["loc_source"] = "city"
            st.success(f"Using searched city: **{meta['name']}**")
        else:
            st.warning("Could not find that city. Please try a different spelling.")
    except Exception as e:
        st.error(f"City lookup failed: {e}")

# ---------- 2) Browser GPS ----------
elif get_loc:
    loc = get_geolocation()  # prompts browser permission
    if loc and "coords" in loc:
        lat = loc["coords"]["latitude"]
        lon = loc["coords"]["longitude"]
        rev = reverse_geocode(lat, lon)
        st.session_state["location"] = {
            "city": rev.get("name"),
            "region": rev.get("admin1"),
            "country": rev.get("country"),
            "coords": (lat, lon),
            "timezone": rev.get("timezone"),
        }
        st.session_state["loc_source"] = "gps"
        st.success(f"Browser location detected: **({lat:.4f}, {lon:.4f})**")
    else:
        st.warning("Could not fetch browser location. Please allow location access in your browser.")

# ---------- 3) Fallback: IP lookup (first run only) ----------
if st.session_state["location"] is None:
    try:
        ip = requests.get("https://ipapi.co/json", timeout=12).json()
        city = ip.get("city") or "Unknown"
        region = ip.get("region") or ""
        country = ip.get("country_name") or ""
        lat = ip.get("latitude")
        lon = ip.get("longitude")
        rev = reverse_geocode(lat, lon) if (lat and lon) else {}
        st.session_state["location"] = {
            "city": rev.get("name") or city,
            "region": rev.get("admin1") or region,
            "country": rev.get("country") or country,
            "coords": (lat, lon) if (lat and lon) else None,
            "timezone": rev.get("timezone") or ip.get("timezone"),
        }
        st.session_state["loc_source"] = "ip"
        st.info("⚠️ Using IP-based location (less accurate). Click **Get my location** in the sidebar and allow access for precise GPS.")
    except Exception as e:
        # Strict fallback → Karachi
        meta = geocode_city("Karachi")
        st.session_state["location"] = {
            "city": meta["name"] if meta else "Karachi",
            "region": (meta or {}).get("admin1"),
            "country": (meta or {}).get("country") or "Pakistan",
            "coords": ((meta or {}).get("lat"), (meta or {}).get("lon")) if meta else None,
            "timezone": (meta or {}).get("timezone") or "Asia/Karachi",
        }
        st.session_state["loc_source"] = "fallback"
        st.warning(f"IP location failed ({e}). Using Karachi as fallback.")

# ---------- Display Active Location ----------
loc = st.session_state["location"] or {}
src = st.session_state["loc_source"] or "unknown"

st.subheader("📍 Active Location")
cols = st.columns(4)
cols[0].metric("Location", loc.get("city") or "—")
cols[1].metric("Region", loc.get("region") or "—")
cols[2].metric("Country", loc.get("country") or "—")
cols[3].metric("Source", src.upper())

if loc.get("coords"):
    lat, lon = loc["coords"]
    st.caption(f"Coords: `{lat:.4f}, {lon:.4f}`")
    st.map(pd.DataFrame([{"lat": lat, "lon": lon}]), zoom=11)

# ---------- Quick Weather ----------
if loc.get("coords"):
    try:
        weather = get_weather(loc["coords"][0], loc["coords"][1], loc.get("timezone"))
        cur = weather.get("current", {}) or {}
        daily = weather.get("daily", {}) or {}
        st.subheader("🛰️ Weather")
        wcols = st.columns(5)
        wcols[0].metric("Temp (°C)", cur.get("temperature_2m"))
        wcols[1].metric("Feels (°C)", cur.get("apparent_temperature"))
        wcols[2].metric("Humidity (%)", cur.get("relative_humidity_2m"))
        wcols[3].metric("Wind (km/h)", cur.get("wind_speed_10m"))
        try:
            hi = (daily.get("temperature_2m_max") or [None])[0]
            lo = (daily.get("temperature_2m_min") or [None])[0]
            wcols[4].metric("Today Hi/Lo", f"{hi} / {lo}")
        except Exception:
            pass
    except Exception as e:
        st.info(f"Weather unavailable right now: {e}")
else:
    st.info("Weather will appear after we have coordinates (use GPS button or search a city).")
