"""Optional: Google Maps Platform Weather API (powered by WeatherNext 3).

NOT USABLE IN KOREA (checked 2026-09-12 with a billing-enabled project): every
Korean coordinate answers 404 "Information is not supported for this location",
while New York and London answer 200 with the same key. Kept for the day the
coverage arrives — or for parks outside Korea.

Free tier: 10,000 calls/month (SKU 'Weather Usage', Essentials); above it $0.15 per
1,000 calls. This client pages 24 hours at a time, so one venue costs
ceil(hours/24) calls — 15 venues × 2 runs × 3 pages ≈ 2,700/month.
Set GOOGLE_WEATHER_API_KEY (or google_weather_key in config.toml) to enable.
The hourly series is mapped onto Open-Meteo variable names so the rest of the
pipeline treats it as one more model ("google_weathernext3").
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

URL = "https://weather.googleapis.com/v1/forecast/hours:lookup"
KST = ZoneInfo("Asia/Seoul")
MODEL_NAME = "google_weathernext3"


def _speed_ms(obj: dict | None) -> float | None:
    if not obj or obj.get("value") is None:
        return None
    v = float(obj["value"])
    unit = obj.get("unit", "KILOMETERS_PER_HOUR")
    return round(v / 3.6, 2) if unit == "KILOMETERS_PER_HOUR" else round(v * 0.44704, 2) if unit == "MILES_PER_HOUR" else v


def hourly(lat: float, lon: float, key: str, hours: int = 48, timeout: int = 30) -> dict:
    """→ {"time": [local ISO hour], "series": {var: [...]}}"""
    rows: list[dict] = []
    token = None
    while True:
        params = {"key": key, "location.latitude": lat, "location.longitude": lon,
                  "hours": hours, "pageSize": 24, "unitsSystem": "METRIC"}
        if token:
            params["pageToken"] = token
        req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params),
                                     headers={"User-Agent": "kbo-weather-forecast/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        rows.extend(data.get("forecastHours", []))
        token = data.get("nextPageToken")
        if not token or len(rows) >= hours:
            break

    time, series = [], {k: [] for k in ("temperature_2m", "relative_humidity_2m", "dew_point_2m",
                                        "precipitation", "precipitation_probability",
                                        "thunderstorm_probability", "pressure_msl", "wind_speed_10m",
                                        "wind_direction_10m", "wind_gusts_10m", "cloud_cover",
                                        "wet_bulb_temperature_2m", "uv_index")}
    for h in rows:
        t = dt.datetime.fromisoformat(h["interval"]["startTime"].replace("Z", "+00:00")).astimezone(KST)
        time.append(t.strftime("%Y-%m-%dT%H:%M"))
        g = lambda *ks: _dig(h, ks)
        series["temperature_2m"].append(g("temperature", "degrees"))
        series["relative_humidity_2m"].append(g("relativeHumidity"))
        series["dew_point_2m"].append(g("dewPoint", "degrees"))
        series["precipitation"].append(g("precipitation", "qpf", "quantity"))
        series["precipitation_probability"].append(g("precipitation", "probability", "percent"))
        series["thunderstorm_probability"].append(g("thunderstormProbability"))
        series["pressure_msl"].append(g("airPressure", "meanSeaLevelMillibars"))
        series["wind_speed_10m"].append(_speed_ms(g("wind", "speed")))
        series["wind_direction_10m"].append(g("wind", "direction", "degrees"))
        series["wind_gusts_10m"].append(_speed_ms(g("wind", "gust")))
        series["cloud_cover"].append(g("cloudCover"))
        series["wet_bulb_temperature_2m"].append(g("wetBulbTemperature", "degrees"))
        series["uv_index"].append(g("uvIndex"))
    return {"time": time, "series": series}


def _dig(obj, keys):
    for k in keys:
        if not isinstance(obj, dict) or k not in obj:
            return None
        obj = obj[k]
    return obj


def merge_into(det: dict, gw: dict) -> None:
    """Align the Google series to Open-Meteo's hourly time axis (in place)."""
    idx = {t: i for i, t in enumerate(gw["time"])}
    aligned = {}
    for var, vals in gw["series"].items():
        aligned[var] = [vals[idx[t]] if t in idx else None for t in det["time"]]
    if any(x is not None for x in aligned["temperature_2m"]):
        det["models"][MODEL_NAME] = aligned
