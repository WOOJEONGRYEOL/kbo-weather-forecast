"""기상청 단기예보·초단기예보 (공공데이터포털 VilageFcstInfoService_2.0).

Free key (자동승인, 개발계정 하루 10,000회). Uses KMA's own 5 km 동네예보
grid. Series are mapped onto Open-Meteo variable names and joined to the
deterministic model table as "kma_short" / "kma_ultra". KMA is the only source
that publishes a calibrated hourly 강수확률 (POP); `rain.py` turns those into a
game-window probability.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
SHORT_ISSUES = (2, 5, 8, 11, 14, 17, 20, 23)       # 단기예보 발표 시각 (10분 뒤 제공)
SKY_CLOUD = {"1": 10.0, "3": 70.0, "4": 95.0}      # 맑음 / 구름많음 / 흐림 → cloud cover %
KMA_VARS = ("temperature_2m", "relative_humidity_2m", "precipitation", "precipitation_probability",
            "wind_speed_10m", "wind_direction_10m", "cloud_cover")


class KMAError(RuntimeError):
    pass


def grid(lat: float, lon: float) -> tuple[int, int]:
    """WGS84 → 기상청 동네예보 5 km LCC 격자 (기상청 공식 변환 상수)."""
    RE, GRID, SLAT1, SLAT2, OLON, OLAT, XO, YO = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    d = math.pi / 180.0
    re_ = RE / GRID
    s1, s2, olon, olat = SLAT1 * d, SLAT2 * d, OLON * d, OLAT * d
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(
        math.tan(math.pi / 4 + s2 / 2) / math.tan(math.pi / 4 + s1 / 2))
    sf = math.tan(math.pi / 4 + s1 / 2) ** sn * math.cos(s1) / sn
    ro = re_ * sf / math.tan(math.pi / 4 + olat / 2) ** sn
    ra = re_ * sf / math.tan(math.pi / 4 + lat * d / 2) ** sn
    th = ((lon * d - olon + math.pi) % (2 * math.pi) - math.pi) * sn
    return (int(math.floor(ra * math.sin(th) + XO + 0.5)),
            int(math.floor(ro - ra * math.cos(th) + YO + 0.5)))


def parse_mm(v) -> float | None:
    """PCP / RN1: '강수없음', '0', '1mm 미만', '3.0mm', '30.0~50.0mm', '50.0mm 이상'."""
    s = str(v if v is not None else "").strip()
    if s in ("", "강수없음", "적설없음", "-"):
        return 0.0
    if "미만" in s:
        return 0.5
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    if not nums:
        return None
    if "~" in s and len(nums) >= 2:
        return (nums[0] + nums[1]) / 2
    return nums[0]


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x >= 900 or x <= -900 else x     # KMA missing-value sentinels


def short_bases(now: dt.datetime) -> list[dt.datetime]:
    """Two newest 단기예보 base times already published (newest first)."""
    c = [dt.datetime.combine(now.date() - dt.timedelta(days=b), dt.time(h))
         for b in (0, 1) for h in SHORT_ISSUES]
    return sorted((x for x in c if x + dt.timedelta(minutes=10) <= now), reverse=True)[:2]


def ultra_bases(now: dt.datetime) -> list[dt.datetime]:
    """초단기예보: issued HH:30, available from HH:45."""
    t = (now - dt.timedelta(minutes=15)).replace(second=0, microsecond=0)
    b = t.replace(minute=30) if t.minute >= 30 else (t - dt.timedelta(hours=1)).replace(minute=30)
    return [b, b - dt.timedelta(hours=1)]


class KMA:
    def __init__(self, key: str, cache_dir: Path | None = None, ttl_s: float = 15 * 60):
        self.key = key
        self.cache_dir = cache_dir
        self.ttl_s = ttl_s
        self.down = False

    def _items(self, op: str, **params) -> list[dict]:
        tag = hashlib.sha1(json.dumps([op, params], sort_keys=True).encode()).hexdigest()[:20]
        cache = self.cache_dir / f"kma_{tag}.json" if self.cache_dir else None
        if cache and cache.exists() and time.time() - cache.stat().st_mtime < self.ttl_s:
            return json.loads(cache.read_text("utf-8"))
        if self.down:
            raise KMAError("기상청 API 연결 불가 (이번 실행에서 이미 실패해 건너뜀)")
        q = {"serviceKey": self.key, "pageNo": 1, "numOfRows": 1500, "dataType": "JSON", **params}
        url = f"{BASE}/{op}?" + urllib.parse.urlencode(q)
        raw, last = None, None
        for _ in range(2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "kbo-weather-forecast/0.1"})
                with urllib.request.urlopen(req, timeout=12) as r:
                    raw = r.read().decode("utf-8", "replace")
                break
            except (urllib.error.URLError, TimeoutError, OSError) as e:   # never echo the URL: it carries the key
                last = e
                time.sleep(2)
        if raw is None:
            self.down = True   # e.g. data.go.kr unreachable from overseas CI — don't stall on every stadium
            raise KMAError(f"기상청 API 연결 실패 ({type(last).__name__})")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = (re.search(r"<returnAuthMsg>([^<]+)</returnAuthMsg>", raw)
                 or re.search(r"<errMsg>([^<]+)</errMsg>", raw))
            raise KMAError(f"기상청 API 오류: {m.group(1) if m else 'XML 응답'}")
        head = data["response"]["header"]
        if head["resultCode"] == "03":                  # NO_DATA: this base time isn't out yet
            return []
        if head["resultCode"] != "00":
            raise KMAError(f"기상청 API 오류 {head['resultCode']}: {head['resultMsg']}")
        items = data["response"]["body"]["items"]["item"]
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(items, ensure_ascii=False), "utf-8")
        return items

    def _series(self, op: str, bases: list[dt.datetime], nx: int, ny: int) -> dict:
        for b in bases:
            items = self._items(op, base_date=b.strftime("%Y%m%d"), base_time=b.strftime("%H%M"), nx=nx, ny=ny)
            if items:
                rows: dict[str, dict] = {}
                for it in items:
                    d, t = it["fcstDate"], it["fcstTime"]
                    rows.setdefault(f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:00", {})[it["category"]] = it["fcstValue"]
                return {"base": b.strftime("%Y-%m-%dT%H:%M"), "rows": rows}
        return {"base": None, "rows": {}}

    def short(self, nx: int, ny: int, now: dt.datetime) -> dict:
        return self._series("getVilageFcst", short_bases(now), nx, ny)

    def ultra(self, nx: int, ny: int, now: dt.datetime) -> dict:
        return self._series("getUltraSrtFcst", ultra_bases(now), nx, ny)


def to_model(rows: dict, times: list[str], temp_key: str, rain_key: str) -> dict:
    """KMA rows → Open-Meteo-style columns aligned to `times` (None where not covered)."""
    cols = {k: [] for k in KMA_VARS}
    for t in times:
        r = rows.get(t) or {}
        cols["temperature_2m"].append(_num(r.get(temp_key)))
        cols["relative_humidity_2m"].append(_num(r.get("REH")))
        cols["precipitation"].append(parse_mm(r[rain_key]) if rain_key in r else None)
        cols["precipitation_probability"].append(_num(r.get("POP")))
        cols["wind_speed_10m"].append(_num(r.get("WSD")))
        cols["wind_direction_10m"].append(_num(r.get("VEC")))
        cols["cloud_cover"].append(SKY_CLOUD.get(str(r.get("SKY"))) if r else None)
    return cols


def merge_into(det: dict, kma: KMA, lat: float, lon: float, now: dt.datetime) -> dict:
    """Add kma_short / kma_ultra to an Open-Meteo det dict (in place)."""
    nx, ny = grid(lat, lon)
    meta: dict = {"nx": nx, "ny": ny}
    short = kma.short(nx, ny, now)
    if short["rows"]:
        det["models"]["kma_short"] = to_model(short["rows"], det["time"], "TMP", "PCP")
        meta["short_base"] = short["base"]
    ultra = kma.ultra(nx, ny, now)
    if ultra["rows"]:
        det["models"]["kma_ultra"] = to_model(ultra["rows"], det["time"], "T1H", "RN1")
        meta["ultra_base"] = ultra["base"]
    det["kma"] = meta
    return meta
