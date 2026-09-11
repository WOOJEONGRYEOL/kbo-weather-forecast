"""Score the rain thresholds against this season's real cancellations.

Uses Open-Meteo's historical-forecast archive (the forecasts as they were
issued, not observations — the same information the 경기운영위원 had) for every
regular-season game, then reports how well "fraction of models predicting a
cancel-class scenario" separates 우천취소/그라운드사정 from played games, and
sweeps thresholds to suggest better ones.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import statistics
from pathlib import Path

from . import rain as rain_mod
from .kbo import Game, _parse
from .openmeteo import OpenMeteo
from .stadiums import Stadium, resolve

MODELS = ["jma_msm", "ecmwf_ifs", "icon_seamless", "gfs_seamless", "ukmo_seamless", "gem_global"]
RAIN_CANCEL = {"우천취소", "그라운드사정"}


def load_games(path: Path) -> list[Game]:
    return [_parse(r) for r in json.loads(path.read_text("utf-8"))]


def season_series(om: OpenMeteo, st: Stadium, start: str, end: str) -> dict:
    out = {}
    for m in MODELS:
        try:
            h = om.historical(st.lat, st.lon, m, start, end)
            if any(v is not None for v in h["precipitation"]):
                out[m] = h
        except Exception:
            continue
    return out


def game_features(hist: dict, g: Game, th: dict, game_len_h: float = 3.5) -> dict | None:
    rows = {}
    for m, h in hist.items():
        times = rain_mod.parse_times(h["time"])
        w = rain_mod.Windows.build(times, g.start, game_len_h)
        if not w.game:
            continue
        s = rain_mod.scenario_stats(h["precipitation"], w)
        rows[m] = {**s, **rain_mod.classify(s, th)}
    if not rows:
        return None
    n = len(rows)
    return {"n_models": n,
            "p_cancel": sum(r["cancel"] for r in rows.values()) / n,
            "p_delay": sum(r["delay"] for r in rows.values()) / n,
            "p_rain": sum(r["rain"] for r in rows.values()) / n,
            "game_mm": statistics.median(r["game"] for r in rows.values()),
            "max_rate": statistics.median(r["max_rate"] for r in rows.values()),
            "pre_mm": statistics.median(r["pre"] for r in rows.values()),
            "models": rows}


def brier(pairs) -> float:
    return statistics.fmean((p - y) ** 2 for p, y in pairs)


def run(games: list[Game], stadiums: dict[str, Stadium], cache_dir: Path, start: str, end: str,
        leagues=(1, 2)) -> dict:
    om = OpenMeteo(cache_dir)
    hist_by_key: dict[str, dict] = {}
    rows = []
    for g in games:
        if g.league not in leagues or g.series != "정규경기" or g.date < start or g.date > end:
            continue
        if g.cancel in ("폭염취소", "미세먼지취소", "기타"):
            continue                         # not a rain decision
        st = resolve(g.stadium_raw, stadiums)
        if st is None:
            continue
        if st.key not in hist_by_key:
            hist_by_key[st.key] = season_series(om, st, start, end)
        th = rain_mod.thresholds_for(g.league, st.drainage)
        f = game_features(hist_by_key[st.key], g, th)
        if f is None:
            continue
        f.update({"date": g.date, "time": g.time, "league": g.league, "stadium": st.key,
                  "label": g.label, "cancel": g.cancel, "y": int(g.cancel in RAIN_CANCEL)})
        rows.append(f)

    # ---- scoring of the current thresholds ---------------------------------
    summary = {"games": len(rows), "rain_cancels": sum(r["y"] for r in rows), "by_league": {}}
    for le in leagues:
        rs = [r for r in rows if r["league"] == le]
        if not rs:
            continue
        base = statistics.fmean(r["y"] for r in rs)
        summary["by_league"][le] = {
            "games": len(rs), "cancels": sum(r["y"] for r in rs), "base_rate": round(base, 3),
            "brier_model": round(brier((r["p_cancel"], r["y"]) for r in rs), 4),
            "brier_climatology": round(brier((base, r["y"]) for r in rs), 4),
            "bins": reliability(rs),
        }
    # ---- threshold sweep (deterministic multi-model fraction as probability) --
    summary["sweep"] = sweep(rows, hist_by_key, stadiums)
    summary["rows"] = rows
    return summary


def reliability(rs: list[dict]) -> list[dict]:
    bins = [(0, .1), (.1, .3), (.3, .5), (.5, .7), (.7, 1.01)]
    out = []
    for lo, hi in bins:
        b = [r for r in rs if lo <= r["p_cancel"] < hi]
        if b:
            out.append({"p_range": f"{lo:.1f}-{min(hi, 1):.1f}", "n": len(b),
                        "observed_cancel_rate": round(statistics.fmean(r["y"] for r in b), 3)})
    return out


def sweep(rows: list[dict], hist_by_key: dict, stadiums: dict[str, Stadium]) -> dict:
    """Grid over (cancel_rate, cancel_total, wet_pre) per league, minimising Brier."""
    results = {}
    for le in (1, 2):
        rs = [r for r in rows if r["league"] == le]
        if len(rs) < 30:
            continue
        best = None
        for cr, ct, wp in itertools.product((1.0, 1.5, 2.0, 3.0, 4.0), (2.0, 3.0, 4.0, 6.0, 8.0, 10.0), (6.0, 10.0, 15.0, 25.0)):
            pairs = []
            for r in rs:
                st = stadiums[r["stadium"]]
                th = rain_mod.thresholds_for(le, st.drainage)
                th.update({"cancel_rate": cr, "cancel_total": ct, "wet_pre": wp * rain_mod.DRAINAGE_FACTOR.get(st.drainage, 1.0)})
                ev = [rain_mod.classify({k: m[k] for k in ("pre", "game", "max_rate", "early_max")}, th)["cancel"]
                      for m in r["models"].values()]
                pairs.append((sum(ev) / len(ev), r["y"]))
            b = brier(pairs)
            if best is None or b < best["brier"]:
                best = {"cancel_rate": cr, "cancel_total": ct, "wet_pre": wp, "brier": round(b, 4)}
        results[le] = best
    return results


def format_summary(s: dict) -> str:
    out = [f"백테스트: 정규경기 {s['games']}건, 우천·그라운드 취소 {s['rain_cancels']}건"]
    for le, d in s["by_league"].items():
        out.append(f"  리그 {le}: {d['games']}경기 / 취소 {d['cancels']} (기본율 {d['base_rate']:.1%}) · "
                   f"Brier 모델 {d['brier_model']} vs 기후값 {d['brier_climatology']}")
        for b in d["bins"]:
            out.append(f"     예측 {b['p_range']}: n={b['n']:4d} 실제 취소율 {b['observed_cancel_rate']:.1%}")
    for le, b in s.get("sweep", {}).items():
        out.append(f"  리그 {le} 최적 임계값(그리드): {b}")
    return "\n".join(out)
