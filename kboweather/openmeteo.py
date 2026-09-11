"""Open-Meteo clients: multi-model deterministic forecast, ensemble members,
and the historical-forecast archive used for back-testing.

Free, no key, ~50 calls/day for the whole league.  Responses are cached on
disk (URL hash → JSON) so re-runs within the TTL cost nothing.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
TZ = "Asia/Seoul"

# Deterministic models that return data for Korea (checked 2026-09-11).
# KMA (기상청) models are suspended on Open-Meteo since the UM→KIM switch
# (2026-04); they are auto-skipped when they come back empty.
DET_MODELS = [
    "jma_msm",          # JMA MSM 5 km  — best native resolution over Korea
    "ecmwf_ifs",        # ECMWF IFS HRES 9 km
    "icon_seamless",    # DWD ICON 11 km
    "ukmo_seamless",    # UKMO global 10 km
    "gfs_seamless",     # NOAA GFS 13 km
    "gem_global",       # ECCC GEM 15 km
    "meteofrance_arpege_world",
    "kma_ldps",         # 기상청 LDAPS 1.5 km (currently empty)
]
# Only what the pipeline reads — every extra variable adds to Open-Meteo's call weight.
DET_VARS = ["temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation",
            "precipitation_probability", "surface_pressure", "pressure_msl", "cloud_cover",
            "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "cape"]

ENS_MODELS = ["ecmwf_ifs025", "ecmwf_aifs025_ensemble", "gfs025", "icon_global",
              "gem_global", "ukmo_global_ensemble_20km"]
ENS_VARS = ["precipitation"]   # members are used only for rain scenarios


class OpenMeteo:
    def __init__(self, cache_dir: Path | None = None, ttl_s: float = 45 * 60):
        self.cache_dir = cache_dir
        self.ttl_s = ttl_s

    # -- plumbing -------------------------------------------------------------
    def _get(self, url: str, params: dict, ttl_s: float | None = None) -> dict:
        q = url + "?" + urllib.parse.urlencode(params, safe=",:")
        ttl = self.ttl_s if ttl_s is None else ttl_s
        cache = None
        if self.cache_dir:
            cache = self.cache_dir / ("om_" + hashlib.sha1(q.encode()).hexdigest()[:20] + ".json")
            if cache.exists() and (ttl < 0 or time.time() - cache.stat().st_mtime < ttl):
                return json.loads(cache.read_text("utf-8"))
        last: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(q, headers={"User-Agent": "kbo-weather-forecast/0.1"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.load(r)
                if cache:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(data), "utf-8")
                return data
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:          # free-tier per-minute budget: wait for the window to reset
                    time.sleep(30 * (attempt + 1))
                elif e.code < 500:         # bad request — retrying won't help
                    break
                else:
                    time.sleep(2 * (attempt + 1))
            except Exception as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Open-Meteo 요청 실패 ({last})\n{q}")

    # -- deterministic multi-model -------------------------------------------
    def forecast(self, lat: float, lon: float, models=DET_MODELS, hourly=DET_VARS,
                 forecast_days: int = 3, past_days: int = 0) -> dict:
        """→ {"time": [...], "elevation": m, "models": {model: {var: [..]}}}
        Models whose series are entirely null are dropped."""
        d = self._get(FORECAST_URL, {
            "latitude": lat, "longitude": lon, "hourly": ",".join(hourly),
            "models": ",".join(models), "timezone": TZ,
            "forecast_days": forecast_days, "past_days": past_days,
            "wind_speed_unit": "ms",
        })
        h = d["hourly"]
        out = {"time": h["time"], "elevation": d.get("elevation"), "models": {}}
        for m in models:
            series = {}
            for v in hourly:
                key = f"{v}_{m}" if len(models) > 1 else v
                if key in h:
                    series[v] = h[key]
            if series and any(x is not None for x in series.get("temperature_2m", [])):
                out["models"][m] = series
        return out

    # -- ensembles -------------------------------------------------------------
    def ensemble(self, lat: float, lon: float, models=ENS_MODELS, hourly=ENS_VARS,
                 forecast_days: int = 3) -> dict:
        """→ {"time": [...], "systems": {system: {var: [member_series, ...]}}}
        Each member series is a list aligned with "time"."""
        d = self._get(ENSEMBLE_URL, {
            "latitude": lat, "longitude": lon, "hourly": ",".join(hourly),
            "models": ",".join(models), "timezone": TZ, "forecast_days": forecast_days,
            "wind_speed_unit": "ms",
        })
        h = d["hourly"]
        systems: dict[str, dict[str, list]] = {}
        for key, series in h.items():
            if key == "time":
                continue
            var = next((v for v in sorted(hourly, key=len, reverse=True) if key.startswith(v + "_")), None)
            if var is None:
                continue
            rest = key[len(var) + 1:]              # e.g. "member03_ecmwf_ifs025_ensemble"
            if rest.startswith("member"):
                system = rest.split("_", 1)[1]
            else:
                system = rest                       # control run
            if not any(x is not None for x in series):
                continue
            systems.setdefault(system, {}).setdefault(var, []).append(series)
        return {"time": h["time"], "systems": systems}

    # -- historical forecasts (for calibration) ------------------------------
    def historical(self, lat: float, lon: float, model: str, start: str, end: str,
                   hourly=("precipitation", "temperature_2m", "relative_humidity_2m",
                           "wind_speed_10m", "wind_direction_10m", "surface_pressure")) -> dict:
        d = self._get(HISTORICAL_URL, {
            "latitude": lat, "longitude": lon, "hourly": ",".join(hourly), "models": model,
            "start_date": start, "end_date": end, "timezone": TZ, "wind_speed_unit": "ms",
        }, ttl_s=-1)  # immutable → cache forever
        return d["hourly"]
