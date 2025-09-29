#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI Travel Assistant — vCloud
- Browser GPS via streamlit-js-eval (accurate)
- Fallbacks: FORCE_* → Browser GPS → IP providers → Karachi
- City search override
- Weather (Open-Meteo) • Restaurants (OSM/Overpass) • Attractions (Wikipedia) • Local News (Google News RSS)
"""

import os, math, time
from urllib.parse import quote_plus
import requests, feedparser
import pandas as pd
import streamlit as st
from streamlit_js_eval import get_geolocation  # client-side GPS

APP_TITLE = "🌍 AI Travel Assistant — vCloud"
USER_AGENT = "AI-Travel-Assistant/Cloud (contact: example@example.com)"

st.set_page_config(page_title="AI Travel Assistant", page_icon="🌍", layout="wide")

# ----------------- Sidebar -----------------
with st.sidebar:
    st.header("⚙️ Controls")
    st.caption("Overrides (optional)")

    city_search = st.text_input("Search city (e.g., Dubai, Karachi, London)")
    search_btn = st.button("Use searched city")

    force_city_ui = st.text_input("FORCE_CITY", value=os.getenv("FORCE_CITY", ""))
    force_coords_ui = st.text_input("FORCE_COORDS (lat,lon)", value=os.getenv("FORCE_COORDS", ""))

    km_box = st.slider("Restaurant search box (km)", 2, 15, 5, 1)
    radius_m = st.slider("Attractions radius (m)", 2000, 20000, 10000, 1000)
    max_rest = st.slider("Max restaurants", 5, 30, 12, 1)
    max_attract = st.slider("Max attractions", 3, 15, 8, 1)
    max_news = st.slider("Max news items", 3, 15, 6, 1)

    st.divider()
    if st.button("📍 Get my location"):
        st.session_state.pop("browser_loc_js", None)
        st.rerun()
    if st.button("↻ Try GPS again"):
        st.session_state.pop("browser_loc_js", None)
        st.rerun()

st.title(APP_TITLE)
st.write("Auto-locate → show **weather**, **nearby restaurants**, **attractions**, and **local news**. Karachi is the strict fallback.")

# ----------------- Geocoding helpers -----------------
@st.cache_data(show_spinner=False, ttl=60*60)
def geocode_city(city: str):
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={quote_plus(city)}&count=1&language=en&format=json"
    r = requests.get(url, timeout=20); r.raise_for_status()
    data = r.json()
    if not data.get("results"):
        return None
    res = data["results"][0]
    return {
        "name": res.get("name"), "country": res.get("country"),
        "lat": float(res.get("latitude")), "lon": float(res.get("longitude")),
        "admin1": res.get("admin1"), "timezone": res.get("timezone"),
        "source": "open-meteo geocode"
    }

@st.cache_data(show_spinner=False, ttl=60*60)
def reverse_geocode(lat: float, lon: float):
    try:
        url = ("https://nominatim.openstreetmap.org/reverse"
               f"?format=json&lat={lat}&lon={lon}&zoom=10&addressdetails=1")
        r = requests.get(url, timeout=12, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
        r.raise_for_status(); j = r.json()
        addr = j.get("address", {}) if isinstance(j, dict) else {}
        name = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or addr.get("county")
        admin1 = addr.get("state") or addr.get("region"); country = addr.get("country")
        if name or admin1 or country:
            return {"name": name, "admin1": admin1, "country": country, "timezone": None, "src": "nominatim"}
    except requests.RequestException:
        pass
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/reverse?latitude={lat}&longitude={lon}&language=en&format=json"
        r = requests.get(url, timeout=12); r.raise_for_status()
        j = r.json()
        if j.get("results"):
            r0 = j["results"][0]
            return {"name": r0.get("name"), "admin1": r0.get("admin1"), "country": r0.get("country"),
                    "timezone": r0.get("timezone"), "src": "open-meteo reverse"}
    except requests.RequestException:
        pass
    return {}

@st.cache_data(show_spinner=False, ttl=15*60)
def _try_ip_providers():
    providers = [
        ("ipapi.co", "https://ipapi.co/json", lambda j: {
            "city": j.get("city"), "region": j.get("region"),
            "country": j.get("country_name") or j.get("country"),
            "lat": float(j["latitude"]), "lon": float(j["longitude"]), "tz": j.get("timezone")
        } if all(k in j for k in ("latitude","longitude")) else None),
        ("ipinfo.io", "https://ipinfo.io/json", lambda j: (
            None if "loc" not in j else {
                "city": j.get("city"), "region": j.get("region"), "country": j.get("country"),
                "lat": float(j["loc"].split(",")[0]), "lon": float(j["loc"].split(",")[1]), "tz": j.get("timezone")
            }
        )),
        ("ipwho.is", "https://ipwho.is", lambda j: (
            None if j.get("success") is False else {
                "city": j.get("city"), "region": j.get("region"), "country": j.get("country"),
                "lat": float(j["latitude"]), "lon": float(j["longitude"]), "tz": j.get("timezone")
            }
        )),
    ]
    for name, url, parser in providers:
        try:
            r = requests.get(url, timeout=10); r.raise_for_status()
            parsed = parser(r.json())
            if parsed and parsed.get("lat") and parsed.get("lon"):
                parsed["source"] = name
                return parsed
        except requests.RequestException:
            continue
    return None

# ----------------- Browser GPS via JS -----------------
def _browser_loc_via_js_eval():
    if "browser_loc_js" in st.session_state:
        return st.session_state["browser_loc_js"]
    try:
        loc = get_geolocation()
        if loc and "coords" in loc:
            c = loc["coords"]
            out = {"lat": float(c["latitude"]), "lon": float(c["longitude"]),
                   "accuracy": float(c.get("accuracy", 0.0)), "method": "js-eval"}
            st.session_state["browser_loc_js"] = out
            return out
    except Exception:
        pass
    st.session_state["browser_loc_js"] = None
    return None

# ----------------- Autodetect -----------------
def autodetect_location(default_city="Karachi", force_city="", force_coords="", browser_loc=None):
    # 0) manual city search
    if city_search and search_btn:
        meta = geocode_city(city_search)
        if meta:
            meta["source"] = "city-search"
            return meta
        else:
            st.warning("City not found; continuing with auto-detection.")

    # 1) FORCE_* overrides
    if force_coords.strip():
        try:
            lat, lon = [float(x) for x in force_coords.split(",")]
            rev = reverse_geocode(lat, lon)
            return {"source": "FORCE_COORDS", "name": rev.get("name") or force_city or default_city,
                    "admin1": rev.get("admin1"), "country": rev.get("country") or "Pakistan",
                    "lat": lat, "lon": lon, "timezone": rev.get("timezone") or "Asia/Karachi"}
        except Exception:
            st.warning("Could not parse FORCE_COORDS. Expected 'lat,lon' (e.g., 25.2048,55.2708 for Dubai).")

    if force_city.strip():
        meta = geocode_city(force_city)
        if meta:
            meta["source"] = "FORCE_CITY"
            return meta
        st.warning("FORCE_CITY not found via geocoder; falling back to auto-detect.")

    # 2) Browser GPS
    if browser_loc and browser_loc.get("lat") and browser_loc.get("lon"):
        lat, lon = float(browser_loc["lat"]), float(browser_loc["lon"])
        rev = reverse_geocode(lat, lon) or {}
        return {"source": f"browser-gps:{browser_loc.get('method','')}",
                "name": rev.get("name") or default_city, "admin1": rev.get("admin1"),
                "country": rev.get("country") or "Pakistan", "lat": lat, "lon": lon,
                "timezone": rev.get("timezone") or "Asia/Karachi"}

    # 3) IP providers
    ip = _try_ip_providers()
    if ip:
        rev = reverse_geocode(ip["lat"], ip["lon"])
        return {"source": f"ip:{ip['source']}", "name": rev.get("name") or ip.get("city") or default_city,
                "admin1": rev.get("admin1") or ip.get("region"),
                "country": rev.get("country") or ip.get("country") or "Pakistan",
                "lat": ip["lat"], "lon": ip["lon"],
                "timezone": rev.get("timezone") or ip.get("tz") or "Asia/Karachi"}

    # 4) Fallback
    meta = geocode_city(default_city)
    if meta:
        meta["source"] = "fallback"
        return meta
    return None

# ----------------- Weather -----------------
@st.cache_data(show_spinner=False, ttl=10*60)
def get_weather(lat: float, lon: float, timezone: str):
    url = ("https://api.open-meteo.com/v1/forecast?"
           f"latitude={lat}&longitude={lon}"
           f"&current=temperature_2m,apparent_temperature,wind_speed_10m,relative_humidity_2m,weather_code"
           f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
           f"&forecast_days=3&timezone={quote_plus(timezone)}")
    r = requests.get(url, timeout=20); r.raise_for_status()
    return r.json()

# ----------------- Utils -----------------
def km_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1); dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2*R*math.asin(math.sqrt(a))

def bbox_from_center(lat, lon, km_box=5):
    dlat = km_box / 111.0
    dlon = km_box / (111.0 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)

# ----------------- Attractions -----------------
@st.cache_data(show_spinner=False, ttl=30*60)
def get_attractions(lat: float, lon: float, radius_m=10000, limit=8):
    url = ("https://en.wikipedia.org/w/api.php?"
           f"action=query&list=geosearch&gscoord={lat}%7C{lon}&gsradius={radius_m}"
           f"&gslimit={limit}&format=json")
    r = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    items = r.json().get("query", {}).get("geosearch", [])
    out = []
    for it in items:
        title = it.get("title")
        if not title: continue
        s_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(title)}"
        try:
            rs = requests.get(s_url, timeout=20, headers={"User-Agent": USER_AGENT})
            if rs.status_code != 200: continue
            summ = rs.json()
            out.append({
                "title": title, "distance_km": (it.get("dist") or 0)/1000.0,
                "summary": summ.get("extract") or "",
                "url": summ.get("content_urls", {}).get("desktop", {}).get("page")
            })
        except requests.RequestException:
            continue
        time.sleep(0.2)
    return out

# ----------------- Restaurants -----------------
@st.cache_data(show_spinner=False, ttl=30*60)
def get_restaurants(lat: float, lon: float, km_box=5, limit=12):
    s, w, n, e = bbox_from_center(lat, lon, km_box)
    overpass_url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="restaurant"]["name"]({s},{w},{n},{e});
      way["amenity"="restaurant"]["name"]({s},{w},{n},{e});
      relation["amenity"="restaurant"]["name"]({s},{w},{n},{e});
    );
    out center;
    """
    r = requests.post(overpass_url, data=query.encode("utf-8"), timeout=60)
    r.raise_for_status()
    rows = []
    for el in r.json().get("elements", []):
        tags = el.get("tags", {}); name = tags.get("name")
        if not name: continue
        if "lat" in el and "lon" in el:
            rlat, rlon = el["lat"], el["lon"]
        else:
            c = el.get("center")
            if not c: continue
            rlat, rlon = c["lat"], c["lon"]
        rows.append({
            "name": name,
            "distance_km": km_distance(lat, lon, rlat, rlon),
            "cuisine": (tags.get("cuisine","").replace("_"," ").title() or "N/A"),
            "website": tags.get("website") or tags.get("contact:website"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "address": ", ".join(filter(None, [
                tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:city")
            ])),
            "lat": rlat, "lon": rlon
        })
    rows.sort(key=lambda x: x["distance_km"])
    return rows[:limit]

# ----------------- News -----------------
@st.cache_data(show_spinner=False, ttl=15*60)
def get_local_news(city: str, max_items=6):
    query = f"{city} when:7d"
    rss = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl=US&ceid=US:en"
    feed = feedparser.parse(rss)
    return [{"title": e.title, "link": e.link, "published": getattr(e, "published", None)}
            for e in feed.entries[:max_items]]

# ----------------- MAIN -----------------
with st.spinner("Detecting your location…"):
    browser_loc = _browser_loc_via_js_eval()
    meta = autodetect_location(
        default_city="Karachi",
        force_city=force_city_ui or os.getenv("FORCE_CITY", ""),
        force_coords=force_coords_ui or os.getenv("FORCE_COORDS", ""),
        browser_loc=browser_loc
    )

if not meta:
    st.error("Could not determine your location. Type a city in the sidebar or allow browser location.")
    st.stop()

if not str(meta.get("source", "")).startswith("browser-gps"):
    st.warning("⚠️ Using IP-based location (less accurate). Click **📍 Get my location** in the sidebar and allow access.")

# Header
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("📌 Location", f"{meta['name'] or 'Unknown'}")
with col2: st.metric("🗺️ Region", f"{(meta.get('admin1') or '')}")
with col3: st.metric("🇺🇳 Country", f"{meta.get('country') or ''}")
with col4: st.metric("⌚ Timezone", f"{meta.get('timezone') or '—'}")
st.caption(f"Source: **{meta.get('source','auto')}** • Coords: `{meta['lat']:.4f}, {meta['lon']:.4f}`")

# Map
st.map(pd.DataFrame([{"lat": meta["lat"], "lon": meta["lon"]}]), size=100, zoom=11)

# Fetch data
with st.spinner("Fetching weather, attractions, restaurants, and local news…"):
    try:
        weather = get_weather(meta["lat"], meta["lon"], meta["timezone"])
        attractions = get_attractions(meta["lat"], meta["lon"], radius_m=radius_m, limit=max_attract)
        restaurants = get_restaurants(meta["lat"], meta["lon"], km_box=km_box, limit=max_rest)
        news = get_local_news(meta["name"], max_items=max_news)
    except requests.HTTPError as e:
        st.error(f"HTTP error: {e}"); st.stop()
    except requests.RequestException as e:
        st.error(f"Network error: {e}"); st.stop()
    except Exception as e:
        st.error(f"Unexpected error: {e}"); st.stop()

# Weather
st.subheader("🛰️ Weather")
cur = weather.get("current", {}) or {}
daily = weather.get("daily", {}) or {}
wcol = st.columns(5)
wcol[0].metric("Temp (°C)", cur.get("temperature_2m"))
wcol[1].metric("Feels (°C)", cur.get("apparent_temperature"))
wcol[2].metric("Humidity (%)", cur.get("relative_humidity_2m"))
wcol[3].metric("Wind (km/h)", cur.get("wind_speed_10m"))
today_hi = (daily.get("temperature_2m_max") or [None])[0]
today_lo = (daily.get("temperature_2m_min") or [None])[0]
today_pr = (daily.get("precipitation_sum") or [None])[0]
wcol[4].metric("Today Hi/Lo", f"{today_hi} / {today_lo}")
st.caption(f"Precipitation (today): **{today_pr} mm**")

with st.expander("Weather (raw JSON)"): st.json(weather)

# Attractions
st.subheader("📍 Attractions (Wikipedia, ~radius)")
if not attractions:
    st.info("No attractions found within the selected radius.")
else:
    for i, a in enumerate(attractions, 1):
        with st.container(border=True):
            st.markdown(f"**{i}. {a['title']}**  ·  ~{a['distance_km']:.2f} km")
            if a.get("summary"): st.write(a["summary"])
            if a.get("url"): st.markdown(f"[Open in Wikipedia]({a['url']})")
with st.expander("Attractions (table)"): st.dataframe(pd.DataFrame(attractions), width="stretch")

# Restaurants
st.subheader("🍽️ Nearby Restaurants (OpenStreetMap)")
if not restaurants:
    st.info("No restaurants found in the current ~box. Try increasing the km range.")
else:
    r_df = pd.DataFrame(restaurants)
    st.dataframe(r_df[["name", "cuisine", "distance_km", "address", "phone", "website"]], width="stretch")
    with st.expander("Plot restaurants on map"):
        if {"lat", "lon"}.issubset(r_df.columns):
            st.map(r_df.rename(columns={"lat": "lat", "lon": "lon"}))
        else:
            st.info("No coordinates available to map.")

# News
st.subheader("📰 Local News (last 7 days)")
if not news:
    st.info("No recent items.")
else:
    for i, n in enumerate(news, 1):
        with st.container():
            when = f" · {n['published']}" if n.get("published") else ""
            st.markdown(f"**{i}. {n['title']}**{when}")
            st.markdown(f"[Read article]({n['link']})")

with st.expander("Debug: detection metadata"): st.json(meta)
st.success("✅ Done.")
st.caption("APIs: Open-Meteo, OpenStreetMap/Overpass, Wikipedia REST/GeoSearch, Google News RSS.")
