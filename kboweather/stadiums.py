"""Venue database: coordinates, field orientation, fences, shelter, drainage.

`data/stadiums.json` is the single source of truth. KBO's schedule names
venues by short strings (S_NM: "잠실", "이천(두산)", ...); `resolve()` maps those.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "stadiums.json"


@dataclass(frozen=True)
class Stadium:
    key: str
    name: str
    short: str
    city: str
    lat: float
    lon: float
    tier: int                    # 1 = 1군 홈구장, 2 = 퓨처스/제2구장
    dome: bool
    cf_azimuth: float | None     # bearing home plate → center field (deg from north)
    azimuth_source: str
    shelter: float               # fraction of ambient 10 m wind felt inside (0 = dome)
    fences: dict                 # {"lf": m, "cf": m, "rf": m, "lc": m|None, "rc": m|None}
    fence_height_m: float | None
    surface: str                 # natural | artificial
    drainage: str                # good | fair | turf
    kbo_names: tuple
    notes: str = ""

    @property
    def wind_note(self) -> str:
        return "돔구장(바람 영향 없음)" if self.dome else f"외야 방위각 {self.cf_azimuth:.0f}°" if self.cf_azimuth is not None else "방위각 미확정"


def load(path: Path = DATA_FILE) -> dict[str, Stadium]:
    raw = json.loads(path.read_text("utf-8"))
    out: dict[str, Stadium] = {}
    for r in raw["stadiums"]:
        r = dict(r)
        r["kbo_names"] = tuple(r.get("kbo_names", []))
        out[r["key"]] = Stadium(**r)
    return out


def resolve(kbo_name: str, stadiums: dict[str, Stadium]) -> Stadium | None:
    name = (kbo_name or "").strip()
    for s in stadiums.values():
        if name in s.kbo_names:
            return s
    for s in stadiums.values():                       # tolerant fallback
        if any(name.startswith(k) or k.startswith(name) for k in s.kbo_names if k):
            return s
    return None


def find(query: str, stadiums: dict[str, Stadium]) -> Stadium | None:
    """CLI lookup by key / short / name substring."""
    q = query.strip().lower()
    for s in stadiums.values():
        if q in (s.key, s.short.lower()) or q in s.name.lower() or q in [k.lower() for k in s.kbo_names]:
            return s
    return None
