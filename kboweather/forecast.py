"""Orchestrator: game + venue → weather → rain / carry / heat → report dict."""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from zoneinfo import ZoneInfo

from . import heat as heat_mod
from . import kma as kma_mod
from . import physics
from . import rain as rain_mod
from .config import Settings
from .kbo import Game
from .openmeteo import OpenMeteo
from .stadiums import Stadium

try:
    from . import googleweather
except Exception:  # pragma: no cover
    googleweather = None

KST = ZoneInfo("Asia/Seoul")


def _median(vals):
    xs = [float(v) for v in vals if v is not None]
    return round(statistics.median(xs), 2) if xs else None


def _mean(vals):
    xs = [float(v) for v in vals if v is not None]
    return round(statistics.fmean(xs), 2) if xs else None


def vector_mean_wind(pairs):
    """pairs of (speed, direction_from). Returns (scalar-mean speed, vector-mean direction)."""
    u = v = 0.0
    speeds = []
    for s, d in pairs:
        if s is None or d is None:
            continue
        r = math.radians(float(d))
        u += -float(s) * math.sin(r)
        v += -float(s) * math.cos(r)
        speeds.append(float(s))
    if not speeds:
        return None, None
    n = len(speeds)
    direction = (math.degrees(math.atan2(-u / n, -v / n)) + 360.0) % 360.0
    return round(statistics.fmean(speeds), 2), round(direction)


def compass(deg: float | None) -> str:
    if deg is None:
        return "-"
    names = ["북", "북북동", "북동", "동북동", "동", "동남동", "남동", "남남동",
             "남", "남남서", "남서", "서남서", "서", "서북서", "북서", "북북서"]
    return names[int((deg + 11.25) // 22.5) % 16]


class WeatherCache:
    """One fetch per venue per run, shared by games at the same park."""

    def __init__(self, om: OpenMeteo, settings: Settings, now: dt.datetime | None = None):
        self.om = om
        self.s = settings
        self.now = now or dt.datetime.now(KST).replace(tzinfo=None)
        self.kma = kma_mod.KMA(settings.kma_service_key, settings.cache_dir) if settings.kma_service_key else None
        self._det: dict[str, dict] = {}
        self._ens: dict[str, dict] = {}

    def det(self, st: Stadium) -> dict:
        if st.key not in self._det:
            d = self.om.forecast(st.lat, st.lon, forecast_days=self.s.forecast_days)
            if self.s.google_weather_key and googleweather:
                try:
                    gw = googleweather.hourly(st.lat, st.lon, self.s.google_weather_key,
                                              hours=24 * self.s.forecast_days)
                    googleweather.merge_into(d, gw)
                except Exception as e:  # never let the optional source break a run
                    d.setdefault("warnings", []).append(f"google weather: {e}")
            if self.kma:
                try:
                    kma_mod.merge_into(d, self.kma, st.lat, st.lon, self.now)
                except Exception as e:  # KMA outage → the other sources still run
                    d.setdefault("warnings", []).append(f"기상청: {str(e).splitlines()[0]}")
            self._det[st.key] = d
        return self._det[st.key]

    def ens(self, st: Stadium) -> dict:
        if st.key not in self._ens:
            try:
                self._ens[st.key] = self.om.ensemble(st.lat, st.lon, forecast_days=self.s.forecast_days)
            except Exception as e:  # degrade to the deterministic models instead of dropping the game
                self._ens[st.key] = {"time": [], "systems": {}, "warning": f"ensemble: {str(e).splitlines()[0]}"}
        return self._ens[st.key]


def conditions_at(det: dict, when: dt.datetime) -> dict:
    """Cross-model consensus for one hour (median for scalars, vector mean for wind)."""
    key = when.strftime("%Y-%m-%dT%H:00")
    if key not in det["time"]:
        return {}
    i = det["time"].index(key)
    ms = det["models"]
    col = lambda var: [m.get(var, [None] * len(det["time"]))[i] for m in ms.values()]
    spd, direction = vector_mean_wind(zip(col("wind_speed_10m"), col("wind_direction_10m")))
    temp = _median(col("temperature_2m"))
    rh = _median(col("relative_humidity_2m"))
    sfc = _median(col("surface_pressure"))
    msl = _median(col("pressure_msl"))
    if sfc is None and msl is not None and temp is not None:
        sfc = round(physics.station_pressure_from_msl(msl, det.get("elevation") or 0.0, temp), 1)
    cloud = _median(col("cloud_cover"))
    return {
        "time": key, "temp": temp, "rh": None if rh is None else round(rh), "dew_point": _median(col("dew_point_2m")),
        "pressure_sfc": sfc, "pressure_msl": msl, "wind_speed": spd, "wind_dir": direction,
        "wind_compass": compass(direction), "wind_gust": _median(col("wind_gusts_10m")),
        "cloud": None if cloud is None else round(cloud), "cape": max((x for x in col("cape") if x is not None), default=None),
        "precip_p50": _median(col("precipitation")),
        "precip_max": max((x for x in col("precipitation") if x is not None), default=None),
        "pop_models": _mean([m.get("precipitation_probability", [None] * len(det["time"]))[i]
                             for name, m in ms.items() if not name.startswith("kma_")]),
        "pop_kma": next((ms[n]["precipitation_probability"][i] for n in ("kma_ultra", "kma_short")
                         if n in ms and ms[n]["precipitation_probability"][i] is not None), None),
        "n_models": len(ms),
    }


def ensemble_wet_fraction(ens: dict, key: str, threshold_mm: float = 0.1) -> float | None:
    if not ens or key not in ens["time"]:
        return None
    i = ens["time"].index(key)
    hit = tot = 0
    for vars_ in ens["systems"].values():
        for m in vars_.get("precipitation", []):
            v = m[i]
            if v is None:
                continue
            tot += 1
            hit += v >= threshold_mm
    return round(hit / tot, 3) if tot else None


def hourly_strip(det: dict, ens: dict, start: dt.datetime, game_len_h: float) -> list[dict]:
    rows = []
    t = start.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
    end = start + dt.timedelta(hours=game_len_h)
    while t <= end:
        c = conditions_at(det, t)
        if c:
            c["p_ens_wet"] = ensemble_wet_fraction(ens, c["time"])
            c["apparent"] = round(heat_mod.kma_apparent(c["temp"], c["rh"]), 1) if c["temp"] is not None and c["rh"] is not None else None
            rows.append(c)
        t += dt.timedelta(hours=1)
    return rows


def game_report(game: Game, st: Stadium, wx: WeatherCache, settings: Settings) -> dict:
    det = wx.det(st)
    ens = wx.ens(st)
    start = game.start
    glen = settings.game_length_h

    rain = rain_mod.outlook(ens, det, start, game.league, st.drainage, glen, now=wx.now)
    if st.dome:  # roof: outdoor rain never stops play (p_rain stays as the fans' commute chance)
        rain.p_delay = rain.p_cancel = rain.p_ground = 0.0
        rain.verdict, rain.verdict_icon = "돔구장 — 비와 무관", "🟢"
    strip = hourly_strip(det, ens, start, glen)
    cond = conditions_at(det, start + dt.timedelta(hours=1)) or (strip[1] if len(strip) > 1 else {})

    carry = None
    if cond.get("temp") is not None and cond.get("pressure_sfc") is not None and cond.get("rh") is not None:
        carry = physics.carry_report(
            cond["temp"], cond["pressure_sfc"], cond["rh"],
            cond.get("wind_speed") or 0.0, cond.get("wind_dir"),
            None if st.dome else st.cf_azimuth, 0.0 if st.dome else st.shelter,
        )
        cf = next(d for d in carry["directions"] if d["direction"] == "CF")
        carry["hr_multiplier"] = round(physics.hr_rate_multiplier(cf["delta_vs_ref_m"], carry["ref_distance_m"]), 3)
        carry["fence_check"] = fence_check(carry, st)

    heat = heat_mod.assess([(r["time"], r["temp"], r["rh"]) for r in strip])

    return {
        "game": game.to_dict(),
        "label": f"{game.away} vs {game.home}",
        "league_name": "KBO 1군" if game.league == 1 else "퓨처스",
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "stadium": {"key": st.key, "name": st.name, "short": st.short, "city": st.city,
                    "lat": st.lat, "lon": st.lon, "dome": st.dome, "cf_azimuth": st.cf_azimuth,
                    "azimuth_source": st.azimuth_source, "shelter": st.shelter, "fences": st.fences,
                    "fence_height_m": st.fence_height_m,
                    "tier": st.tier, "surface": st.surface, "drainage": st.drainage, "notes": st.notes,
                    "elevation_m": det.get("elevation")},
        "conditions": cond,
        "hourly": strip,
        "rain": rain.to_dict(),
        "carry": carry,
        "heat": heat,
        "sources": {"det_models": list(det["models"].keys()), "kma": det.get("kma"),
                    "ens_systems": {k: len(v.get("precipitation", [])) for k, v in ens.get("systems", {}).items()},
                    "warnings": det.get("warnings", []) + ([ens["warning"]] if ens.get("warning") else [])},
    }


def fence_check(carry: dict, st: Stadium) -> dict:
    """Does today's standard fly ball clear each fence? (rough, ignores height)."""
    out = {}
    for d in carry["directions"]:
        fence = st.fences.get({"LF": "lf", "CF": "cf", "RF": "rf"}[d["direction"]])
        if fence:
            row = {"fence_m": fence, "margin_m": round(d["distance_m"] - fence, 1),
                   "ref_margin_m": round(carry["ref_distance_m"] - fence, 1)}
            if d["direction"] == "CF":     # 중앙만 궤적을 갖고 있음
                wall = getattr(st, "fence_height_m", None)
                z_today = physics.height_at(carry.get("path_cf") or [], fence)
                z_ref = physics.height_at(carry.get("path_ref") or [], fence)
                row.update(height_today_m=z_today, height_ref_m=z_ref, wall_m=wall)
                if wall and z_today is not None:
                    row["clears"] = z_today > wall
                    row["over_wall_m"] = round(z_today - wall, 2)
            out[d["direction"]] = row
    return out


def log_history(day: dict, path) -> None:
    """One line per outdoor game with every source's probabilities and the lead
    time, so `verify` can later score 기상청 vs 앙상블 vs the blend on real results."""
    rows = []
    for r in day["reports"]:
        g, rain = r["game"], r["rain"]
        if r["stadium"]["dome"] or g["game_id"] == "adhoc" or rain.get("lead_h") is None or rain["lead_h"] < 0:
            continue
        rows.append({"run_at": day["generated_at"], "game_id": g["game_id"], "date": g["date"], "time": g["time"],
                     "league": g["league"], "stadium": r["stadium"]["key"], "lead_h": rain["lead_h"],
                     "final": {k: rain[f"p_{k}"] for k in ("rain", "delay", "cancel", "ground")},
                     "ens": rain.get("p_ens"), "det": rain.get("p_det"), "kma": rain.get("p_kma"),
                     "weights": rain.get("weights")})
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in rows)


def run_day(date: dt.date, games: list[Game], stadiums: dict[str, Stadium], settings: Settings,
            leagues=(1, 2)) -> dict:
    from .stadiums import resolve
    om = OpenMeteo(settings.cache_dir)
    wx = WeatherCache(om, settings)
    reports, skipped = [], []
    for g in games:
        if g.league not in leagues:
            continue
        st = resolve(g.stadium_raw, stadiums)
        if st is None:
            skipped.append({"game": g.to_dict(), "reason": f"unknown venue '{g.stadium_raw}'"})
            continue
        try:
            reports.append(game_report(g, st, wx, settings))
        except Exception as e:
            skipped.append({"game": g.to_dict(), "reason": (str(e).splitlines() or [type(e).__name__])[0][:160]})
    day = {
        "date": date.isoformat(),
        "generated_at": wx.now.strftime("%Y-%m-%dT%H:%M:%S"),
        "reports": reports,
        "skipped": skipped,
    }
    log_history(day, settings.cache_dir.parent / "history" / "forecasts.jsonl")
    return day
