"""Rain-out / rain-delay probabilities from ensemble members + model agreement.

Every ensemble member (≈212 across 6 systems) is a self-consistent scenario.
For each we accumulate precipitation over three windows around first pitch:

  pre   : [start − 7 h, start − 1 h)   field condition (그라운드 사정)
  game  : [start − 1 h, start + 3.5 h)  will it rain on the game
  early : [start,       start + 2 h)    would it rain before 5 innings (정식 경기)

then classify the scenario with league-specific thresholds. Probabilities are
the fraction of scenarios per system (systems weighted equally so ECMWF's 51
members do not drown UKMO's 18), blended 75/25 with the fraction of high-res
deterministic models that agree.

The thresholds are a documented prior, not a fit; `backtest.py` scores them
against the season's real 우천취소 decisions and can suggest better ones.
"""
from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field, asdict

# mm or mm/h.  league 2 (퓨처스) cancels more readily — no gate money, day games.
# Back-tested 2026-03-20..09-10 against KBO's real 우천취소/그라운드사정 decisions
# (1316 regular-season games, 140 rain cancellations) with the historical-forecast
# archive: Brier 0.029 vs climatology 0.057 (1군), 0.077 vs 0.129 (퓨처스).
# cancel_rate / cancel_total / wet_pre are the in-sample grid optimum; the rest are priors.
THRESHOLDS = {
    1: {"rain_total": 0.5, "delay_rate": 1.5, "cancel_rate": 3.0, "cancel_total": 6.0,
        "wet_pre": 25.0, "ground_pre": 25.0},
    2: {"rain_total": 0.5, "delay_rate": 1.0, "cancel_rate": 3.0, "cancel_total": 4.0,
        "wet_pre": 6.0, "ground_pre": 15.0},
}
DRAINAGE_FACTOR = {"good": 1.0, "fair": 0.8, "turf": 1.3}   # scales the pre-game thresholds
ENS_WEIGHT = 0.75


def thresholds_for(league: int, drainage: str) -> dict:
    th = dict(THRESHOLDS.get(league, THRESHOLDS[1]))
    f = DRAINAGE_FACTOR.get(drainage, 1.0)
    th["wet_pre"] *= f
    th["ground_pre"] *= f
    return th


def parse_times(times: list[str]) -> list[dt.datetime]:
    return [dt.datetime.fromisoformat(t) for t in times]


def slots(times: list[dt.datetime], t0: dt.datetime, t1: dt.datetime) -> list[int]:
    """Indices of hourly slots [t, t+1h) overlapping [t0, t1)."""
    one = dt.timedelta(hours=1)
    return [i for i, t in enumerate(times) if t < t1 and t + one > t0]


@dataclass
class Windows:
    pre: list[int]
    game: list[int]
    early: list[int]
    start: dt.datetime
    end: dt.datetime

    @classmethod
    def build(cls, times: list[dt.datetime], start: dt.datetime, game_len_h: float = 3.5) -> "Windows":
        h = dt.timedelta(hours=1)
        return cls(
            pre=slots(times, start - 7 * h, start - 1 * h),
            game=slots(times, start - 1 * h, start + dt.timedelta(hours=game_len_h)),
            early=slots(times, start, start + 2 * h),
            start=start, end=start + dt.timedelta(hours=game_len_h),
        )


def scenario_stats(series: list, w: Windows) -> dict:
    v = lambda i: 0.0 if series[i] is None else float(series[i])
    return {
        "pre": sum(v(i) for i in w.pre),
        "game": sum(v(i) for i in w.game),
        "max_rate": max((v(i) for i in w.game), default=0.0),
        "early_max": max((v(i) for i in w.early), default=0.0),
    }


def classify(s: dict, th: dict) -> dict:
    rain = s["game"] >= th["rain_total"]
    delay = s["max_rate"] >= th["delay_rate"]
    ground = s["pre"] >= th["ground_pre"]
    cancel = (s["early_max"] >= th["cancel_rate"] or s["game"] >= th["cancel_total"]
              or (s["pre"] >= th["wet_pre"] and rain) or ground)
    return {"rain": rain, "delay": delay, "cancel": cancel, "ground": ground}


EVENTS = ("rain", "delay", "cancel", "ground")


@dataclass
class Outlook:
    p_rain: float
    p_delay: float
    p_cancel: float
    p_ground: float
    verdict: str
    verdict_icon: str
    members: int
    systems: dict = field(default_factory=dict)      # per-system fractions
    det: dict = field(default_factory=dict)          # per-model scenario + events
    p_ens: dict | None = None
    p_det: dict | None = None
    game_mm: dict | None = None                      # p10/p50/p90 over all members
    pre_mm_p50: float | None = None
    thresholds: dict = field(default_factory=dict)
    window: tuple[str, str] = ("", "")
    p_kma: dict | None = None                        # KMA's own view (POP-based)
    kma: dict | None = None                          # KMA hourly POP / amounts / source
    weights: dict = field(default_factory=dict)      # effective share of each source
    lead_h: float | None = None                      # hours from this run to first pitch

    def to_dict(self) -> dict:
        return asdict(self)


def verdict_for(p_cancel: float, p_delay: float, p_rain: float) -> tuple[str, str]:
    if p_cancel >= 0.5:
        return "우천 취소 유력", "🔴"
    if p_cancel >= 0.25 or p_delay >= 0.5:
        return "우천 변수 큼 (중단·취소 가능)", "🟠"
    if p_rain >= 0.3 or p_delay >= 0.2:
        return "비 가능성 — 우비 챙기기", "🟡"
    return "강수 걱정 없음", "🟢"


KMA_MODELS = ("kma_ultra", "kma_short")      # ultra overrides short for the hours it covers


def kma_weight(lead_h: float | None) -> float:
    """Share of the final probability given to KMA's own forecast — larger as
    first pitch nears (초단기예보 + forecaster updates). A prior, not a fit:
    `python -m kboweather verify` scores every source as games accumulate."""
    if lead_h is None:
        return 0.0
    if lead_h <= 6:
        return 0.5
    if lead_h <= 24:
        return 0.35
    return 0.25


def kma_component(det: dict, start: dt.datetime, th: dict, game_len_h: float) -> dict | None:
    """KMA's view of the game window: hourly POP → window probability, KMA
    amounts → whether that rain would stop or cancel play."""
    ms = (det or {}).get("models", {})
    srcs = [m for m in KMA_MODELS if m in ms]
    if not srcs:
        return None
    n = len(det["time"])
    w = Windows.build(parse_times(det["time"]), start, game_len_h)

    def merged(var: str) -> list:
        out = []
        for i in range(n):
            out.append(next((ms[m][var][i] for m in srcs if ms[m].get(var) and ms[m][var][i] is not None), None))
        return out

    pop, mm = merged("precipitation_probability"), merged("precipitation")
    hours = [(det["time"][i], pop[i], mm[i]) for i in w.game if pop[i] is not None]
    if not hours:
        return None
    ps = [p / 100.0 for _, p, _ in hours]
    # KMA's hourly POPs are smoothed and strongly correlated hour to hour (the same
    # shower risk repeated), so the window probability is the highest hourly POP —
    # treating the hours as independent chances double-counts (20 % × 5 h → 67 %).
    p_rain = max(ps)
    s = scenario_stats(mm, w)
    ev = classify(s, th)
    pre_known = bool(w.pre) and all(mm[i] is not None for i in w.pre)
    ultra_used = "kma_ultra" in srcs and any(ms["kma_ultra"]["precipitation_probability"][i] is not None for i in w.game)
    return {
        "p": {"rain": round(p_rain, 3),
              "delay": round(p_rain if ev["delay"] else 0.0, 3),
              "cancel": round(p_rain if ev["cancel"] else 0.0, 3),
              "ground": (1.0 if ev["ground"] else 0.0) if pre_known else None},
        "pop_max": round(max(ps) * 100),
        "hours": [{"time": t[11:16], "pop": p, "mm": m} for t, p, m in hours],
        "game_mm": round(s["game"], 1), "max_rate": round(s["max_rate"], 1),
        "source": "초단기·단기예보" if ultra_used else "단기예보",
        "coverage": [len(hours), len(w.game)],
    }


def outlook(ens: dict, det: dict, start: dt.datetime, league: int, drainage: str = "good",
            game_len_h: float = 3.5, now: dt.datetime | None = None) -> Outlook:
    th = thresholds_for(league, drainage)
    systems: dict = {}
    all_game: list[float] = []
    all_pre: list[float] = []
    n_members = 0
    if ens and ens.get("systems"):
        times = parse_times(ens["time"])
        w = Windows.build(times, start, game_len_h)
        if w.game:
            for name, vars_ in ens["systems"].items():
                members = vars_.get("precipitation") or []
                if not members:
                    continue
                st = [scenario_stats(m, w) for m in members]
                ev = [classify(s, th) for s in st]
                systems[name] = {"n": len(members),
                                 **{k: round(sum(e[k] for e in ev) / len(ev), 3) for k in EVENTS},
                                 "game_mm_p50": round(statistics.median(s["game"] for s in st), 1)}
                all_game.extend(s["game"] for s in st)
                all_pre.extend(s["pre"] for s in st)
                n_members += len(members)
    p_ens = {k: statistics.fmean(v[k] for v in systems.values()) for k in EVENTS} if systems else None

    det_rows: dict = {}
    if det and det.get("models"):
        times_d = parse_times(det["time"])
        wd = Windows.build(times_d, start, game_len_h)
        if wd.game:
            for model, series in det["models"].items():
                if model in KMA_MODELS:
                    continue                    # KMA is blended separately (POP-based)
                p = series.get("precipitation")
                if not p or all(x is None for x in p):
                    continue
                s = scenario_stats(p, wd)
                det_rows[model] = {**{k: round(v, 2) for k, v in s.items()}, **classify(s, th)}
    p_det = {k: statistics.fmean(r[k] for r in det_rows.values()) for k in EVENTS} if det_rows else None

    def blend(k: str) -> float:
        if p_ens is not None and p_det is not None:
            return ENS_WEIGHT * p_ens[k] + (1 - ENS_WEIGHT) * p_det[k]
        return (p_ens or p_det or {}).get(k, 0.0)

    kma = kma_component(det, start, th, game_len_h)
    lead_h = round((start - now).total_seconds() / 3600, 1) if now else None
    has_base = bool(systems or det_rows)
    w_k = (kma_weight(lead_h) if has_base else 1.0) if kma else 0.0
    p = {}
    for k in EVENTS:
        pk = kma["p"][k] if kma else None
        if pk is None or not w_k:
            p[k] = round(blend(k), 3)
        else:
            p[k] = round(w_k * pk + (1 - w_k) * (blend(k) if has_base else 0.0), 3)
    rest = 1 - w_k if has_base else 0.0
    if p_ens is not None and p_det is not None:
        weights = {"kma": w_k, "ens": round(rest * ENS_WEIGHT, 3), "det": round(rest * (1 - ENS_WEIGHT), 3)}
    else:
        weights = {"kma": w_k, "ens": round(rest, 3) if p_ens is not None else 0.0,
                   "det": round(rest, 3) if p_det is not None else 0.0}
    if not systems and not det_rows and not kma:
        verdict, icon = "예보 범위 밖 (forecast_days 늘리기)", "⚪"
    else:
        verdict, icon = verdict_for(p["cancel"], p["delay"], p["rain"])
    q = None
    if all_game:
        xs = sorted(all_game)
        pick = lambda f: xs[min(len(xs) - 1, int(f * len(xs)))]
        q = {"p10": round(pick(0.10), 1), "p50": round(pick(0.50), 1), "p90": round(pick(0.90), 1),
             "max": round(xs[-1], 1)}
    return Outlook(
        p_rain=p["rain"], p_delay=p["delay"], p_cancel=p["cancel"], p_ground=p["ground"],
        verdict=verdict, verdict_icon=icon, members=n_members, systems=systems, det=det_rows,
        p_ens={k: round(v, 3) for k, v in p_ens.items()} if p_ens else None,
        p_det={k: round(v, 3) for k, v in p_det.items()} if p_det else None,
        game_mm=q, pre_mm_p50=round(statistics.median(all_pre), 1) if all_pre else None,
        thresholds=th,
        window=((start - dt.timedelta(hours=1)).strftime("%H:%M"),
                (start + dt.timedelta(hours=game_len_h)).strftime("%H:%M")),
        p_kma=kma["p"] if kma else None, kma=kma, weights=weights, lead_h=lead_h,
    )
