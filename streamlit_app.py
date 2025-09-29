#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI Travel Assistant — vCloud (location fix)
- Primary: precise browser GPS via streamlit-js-eval (Promise-based)
- Fallbacks: multi-provider IP (ipapi -> ipinfo -> ipwho), then hard fallback = Karachi
- Reverse geocoding: Nominatim (English) with Open-Meteo backup
- Manual city search override
"""

import os
import math
import requests
import pandas as pd
import streamlit as st
from urllib.parse import quote_plus

# ---- JS bridge for geolocation (Promise so we actually get a value) ----
# streamlit-js-eval >= 0.1.7 required
from streamlit_js_eval import streamlit_js_eval

APP_TITLE = "🌍 AI Travel Assistant — vCloud"
USER_AGENT = "ai-travel-assistant/vcloud (contact: you@example.com)"

st.set_page_config(page_title="AI Travel Assistant", layout="wide")

# ======================= Geocoding helpers =======================
@st.cache_data(show_spinner=False, ttl=60 * 60)
def geocode_city(city: str):
    """Open-Meteo forward geocoder."""
    try:
        url = (
            "https://geocoding-api.open-meteo.com/v1/search"
            f"?name={quote_plus(city)}&count=1&language=en&format=json"
        )
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data.get("results"):
            return None
        res = data["results"][0]
        return {
            "name": res.get("name"),
            "admin1": res.get("admin1"),
            "country": res.get("country"),
            "lat": float(res["latitude"]),
            "lon": float(res["longitude"]),
            "timezone": res.get("timezone"),
            "source": "search:open-meteo",
        }
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=60 * 60)
def reverse_geocode(lat: float, lon: float):
    """Try Nominatim; fall back to Open-Meteo reverse."""
    # Nominatim (English)
    try:
        nomi = (
            "https://nominatim.openstreetmap.org/reverse"
            f"?format=json&lat={lat}&lon={lon}&zoom=10&addressdetails=1"
        )
        r = requests.get(
            nomi,
            timeout=12,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
        )
        r.raise_for_status()
        j = r.json()
        addr = j.get("address", {}) if isinstance(j, dict) else {}
        name = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("municipality")
            or addr.get("county")
        )
        admin1 = addr.get("state") or addr.get("region")
        country = addr.get("country")
        if name or admin1 or country:
            return {
                "name": name,
                "admin1": admin1,
                "country": country,
                "timezone": None,
                "source": "reverse:nominatim",
            }
    except Exception:
        pass

    # Open-Meteo reverse backup
    try:
        url = (
            "https://geocoding-api.open-meteo.com/v1/reverse"
            f"?latitude={lat}&longitude={lon}&language=en&format=json"
        )
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
                "source": "reverse:open-meteo",
            }
    except Exception:
        pass

    return {}


@st.cache_data(show_spinner=False, ttl=15 * 60)
def ip_detect():
    """Try multiple IP providers for coarse location."""
    providers = [
        ("ipapi.co", "https://ipapi.co/json", lambda j: {
            "city": j.get("city"),
            "admin1": j.get("region"),
            "country": j.get("country_name") or j.get("country"),
            "lat": j.get("latitude"),
            "lon": j.get("longitude"),
            "timezone": j.get("timezone")
        }),
        ("ipinfo.io", "https://ipinfo.io/json", lambda j: None if "loc" not in j else {
            "city": j.get("city"),
            "admin1": j.get("region"),
            "country": j.get("country"),
            "lat": float(j["loc"].split(",")[0]),
            "lon": float(j["loc"].split(",")[1]),
            "timezone": j.get("timezone")
        }),
        ("ipwho.is", "https://ipwho.is", lambda j: None if j.get("success") is False else {
            "city": j.get("city"),
            "admin1": j.get("region"),
            "country": j.get("country"),
            "lat": j.get("latitude"),
            "lon": j.get("longitude"),
            "timezone": j.get("timezone")
        }),
    ]
    for name, url, parser in providers:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            parsed = parser(r.json())
            if parsed and parsed.get("lat") and parsed.get("lon"):
                parsed["source"] = f"ip:{name}"
                # normalize floats
                parsed["lat"] = float(parsed["lat"])
                parsed["lon"] = float(parsed["lon"])
                return parsed
        except Exception:
            continue
    return None


def set_session_location(meta: dict):
    """Normalize and store into session."""
    st.session_state["location"] = {
        "name": meta.get("name"),
        "admin1": meta.get("admin1"),
        "country": meta.get("country"),
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "timezone": meta.get("timezone"),
        "source": meta.get("source"),
    }


# ======================= UI =======================
st.title(APP_TITLE)
st.caption(
    "Auto-locate → show weather, nearby restaurants, attractions, and local news. Karachi is the strict fallback."
)

with st.sidebar:
    st.header("Controls")
    query_city = st.text_input("Search city (e.g., Dubai, Karachi, London)")
    use_city_btn = st.button("Use searched city")
    st.divider()

    st.subheader("📍 Location options")
    get_loc_btn = st.button("Get my location (GPS)")
    st.caption(
        "If clicking does nothing: allow location access in the site permissions (🔒 icon) and click again."
    )

# 1) Manual typed city
if use_city_btn and query_city.strip():
    g = geocode_city(query_city.strip())
    if g:
        g["source"] = "manual:search"
        set_session_location(g)
        st.success(f"Using searched city: **{g['name']}**")
    else:
        st.warning("Could not find that city. Check the spelling and try again.")

# 2) Browser GPS via JS (Promise)
elif get_loc_btn:
    js = """
    new Promise((resolve) => {
      try {
        if (!navigator.geolocation) {
          resolve({error: "Geolocation API not available"});
          return;
        }
        navigator.geolocation.getCurrentPosition(
          (pos) => resolve({
            lat: pos.coords.latitude,
            lon: pos.coords.longitude,
            acc: pos.coords.accuracy
          }),
          (err) => resolve({error: err && err.message ? err.message : "permission denied"})
        );
      } catch (e) {
        resolve({error: e.message || "unknown error"});
      }
    })
    """
    result = streamlit_js_eval(js_expressions=js, key="get_gps_v2", want_output=True)
    if isinstance(result, dict) and "lat" in result and "lon" in result:
        lat, lon = float(result["lat"]), float(result["lon"])
        rev = reverse_geocode(lat, lon) or {}
        meta = {
            "name": rev.get("name"),
            "admin1": rev.get("admin1"),
            "country": rev.get("country"),
            "lat": lat,
            "lon": lon,
            "timezone": rev.get("timezone"),
            "source": "browser:gps",
        }
        set_session_location(meta)
        st.success(f"Browser location detected: **{lat:.4f}, {lon:.4f}**")
    else:
        st.warning("Could not fetch browser location. Please allow access and click again.")

# 3) First load / fallback chain
if "location" not in st.session_state:
    ip = ip_detect()
    if ip:
        # Improve IP result with reverse geocode for nicer city label
        rev = reverse_geocode(ip["lat"], ip["lon"]) or {}
        ip["name"] = rev.get("name") or ip.get("city")
        ip["admin1"] = rev.get("admin1") or ip.get("admin1")
        ip["country"] = rev.get("country") or ip.get("country")
        set_session_location(ip)
        st.warning("⚠️ Using IP-based location (less accurate).")
    else:
        # hard fallback to Karachi
        k = geocode_city("Karachi") or {
            "name": "Karachi",
            "admin1": "Sindh",
            "country": "Pakistan",
            "lat": 24.8607,
            "lon": 67.0011,
            "timezone": "Asia/Karachi",
        }
        k["source"] = "fallback"
        set_session_location(k)
        st.warning("Using strict fallback: Karachi.")

# ======================= Display Active Location =======================
meta = st.session_state["location"]
st.subheader("📍 Active Location")

cols = st.columns(4)
cols[0].metric("Location", meta.get("name") or "—")
cols[1].metric("Region", meta.get("admin1") or "—")
cols[2].metric("Country", meta.get("country") or "—")
cols[3].metric("Timezone", meta.get("timezone") or "—")

st.caption(
    f"Source: **{meta.get('source','?')}** • Coords: `{meta.get('lat'):.4f}, {meta.get('lon'):.4f}`"
    if meta.get("lat") and meta.get("lon")
    else f"Source: **{meta.get('source','?')}**"
)

# A tiny map pin (Streamlit's built-in map)
try:
    df = pd.DataFrame([{"lat": meta.get("lat"), "lon": meta.get("lon")}])
    if pd.notna(df.iloc[0]["lat"]) and pd.notna(df.iloc[0]["lon"]):
        st.map(df, size=100, zoom=11)  # Streamlit 1.50 still supports this; OK to ignore deprecation notice.
except Exception:
    pass

with st.expander("Debug: raw location dict"):
    st.json(meta)
