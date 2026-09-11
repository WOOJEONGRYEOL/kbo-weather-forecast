"""KMA summer apparent temperature (체감온도, 2022 revision) + KBO heat tiers.

체감온도 = -0.2442 + 0.55399·Tw + 0.45535·Ta − 0.0022·Tw² + 0.00278·Tw·Ta + 3.0
Tw (wet-bulb) from Stull (2011) using air temperature and relative humidity.

KBO tiers follow the August 2026 revision (체감온도 기준). Thresholds live in
`LEVELS` so they can be re-tuned when KBO changes the rule again.
"""
from __future__ import annotations

import math

LEVELS = [
    (35.0, "danger", "체감 35℃ 이상 — 폭염 취소 가능 구간 (2026-08 개정: 당일 13:00 취소 결정)"),
    (33.0, "warning", "체감 33℃ 이상 — 폭염특보 수준, 쿨링브레이크·개시 최대 1시간 지연 가능"),
    (31.0, "caution", "체감 31℃ 이상 — 더위 주의 (관중 온열 위험)"),
    (-99.0, "ok", "폭염 변수 없음"),
]


def wet_bulb_stull(t_c: float, rh: float) -> float:
    rh = max(1.0, min(100.0, rh))
    return (t_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
            + math.atan(t_c + rh) - math.atan(rh - 1.67633)
            + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def kma_apparent(t_c: float, rh: float) -> float:
    tw = wet_bulb_stull(t_c, rh)
    return -0.2442 + 0.55399 * tw + 0.45535 * t_c - 0.0022 * tw * tw + 0.00278 * tw * t_c + 3.0


def assess(hours: list[tuple[str, float | None, float | None]]) -> dict:
    """hours: [(iso_time, temp_c, rh)] over the game window → worst hour + tier."""
    rows = [(tm, kma_apparent(t, rh), t, rh) for tm, t, rh in hours if t is not None and rh is not None]
    if not rows:
        return {"level": "unknown", "text": "체감온도 계산 불가"}
    tm, amax, t, rh = max(rows, key=lambda r: r[1])
    for thr, level, text in LEVELS:
        if amax >= thr:
            return {"level": level, "max_apparent": round(amax, 1), "at": tm,
                    "temp": round(t, 1), "rh": round(rh), "text": text}
    return {"level": "ok", "text": LEVELS[-1][2]}
