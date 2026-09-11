"""KBO schedule client (1군 + 퓨처스) — koreabaseball.com's own JSON endpoint.

No API key. One POST per (league, date). Results are cached on disk so the
season back-test does not hammer the site.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

GAMELIST_URL = "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList"
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.koreabaseball.com/",
    "User-Agent": "Mozilla/5.0 (kbo-weather-forecast; personal, non-commercial)",
}
LEAGUE_NAMES = {1: "KBO 1군", 2: "퓨처스"}

# GAME_STATE_SC: 1 = 예정, 2 = 진행중, 3 = 종료 (관측 기준), 4/5 = 취소 계열
STATE_NAMES = {"1": "예정", "2": "진행중", "3": "종료", "4": "취소", "5": "취소"}


@dataclass
class Game:
    game_id: str
    date: str            # YYYY-MM-DD
    time: str            # HH:MM (local)
    league: int          # 1 or 2
    stadium_raw: str     # KBO's S_NM, e.g. "잠실", "이천(두산)"
    home: str
    away: str
    state: str
    cancel: str          # "정상경기" | "우천취소" | "폭염취소" | "그라운드사정" | ...
    series: str          # 정규경기 / 시범경기 / 올스타 / 포스트시즌 ...
    group: str | None = None      # 퓨처스 북부/남부
    home_starter: str | None = None
    away_starter: str | None = None
    tv: str | None = None

    @property
    def start(self) -> dt.datetime:
        return dt.datetime.strptime(f"{self.date} {self.time}", "%Y-%m-%d %H:%M")

    @property
    def label(self) -> str:
        return f"{self.away} @ {self.home}"

    def to_dict(self) -> dict:
        return asdict(self)


def _parse(row: dict) -> Game:
    d = row["G_DT"]
    return Game(
        game_id=row["G_ID"],
        date=f"{d[:4]}-{d[4:6]}-{d[6:]}",
        time=(row.get("G_TM") or "00:00").strip(),
        league=int(row["LE_ID"]),
        stadium_raw=(row.get("S_NM") or "").strip(),
        home=(row.get("HOME_NM") or "").strip(),
        away=(row.get("AWAY_NM") or "").strip(),
        state=str(row.get("GAME_STATE_SC") or ""),
        cancel=(row.get("CANCEL_SC_NM") or "").strip(),
        series=(row.get("GAME_SC_NM") or "").strip(),
        group=row.get("B_GROUP_SC") or row.get("T_GROUP_SC"),
        home_starter=(row.get("B_PIT_P_NM") or "").strip() or None,
        away_starter=(row.get("T_PIT_P_NM") or "").strip() or None,
        tv=row.get("TV_IF") or None,
    )


def fetch_games(date: dt.date, league: int, cache_dir: Path | None = None,
                cache_ttl_s: float = 20 * 60, retries: int = 3) -> list[Game]:
    """Games of one league on one date. Past dates are cached forever."""
    key = f"kbo_{league}_{date:%Y%m%d}.json"
    cache = cache_dir / key if cache_dir else None
    if cache and cache.exists():
        age = time.time() - cache.stat().st_mtime
        if date < dt.date.today() or age < cache_ttl_s:
            return [_parse(r) for r in json.loads(cache.read_text("utf-8"))]
    body = urllib.parse.urlencode({"leId": league, "srId": "0,1,3,4,5,6,7,8,9",
                                   "date": f"{date:%Y%m%d}"}).encode()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(GAMELIST_URL, data=body, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as r:
                rows = json.load(r).get("game", [])
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(rows, ensure_ascii=False), "utf-8")
            return [_parse(r) for r in rows]
        except Exception as e:  # network hiccup → retry with backoff
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"KBO schedule fetch failed for {date} league {league}: {last_err}")


def games_on(date: dt.date, leagues=(1, 2), cache_dir: Path | None = None) -> list[Game]:
    out: list[Game] = []
    for le in leagues:
        out.extend(fetch_games(date, le, cache_dir))
    out.sort(key=lambda g: (g.time, g.league, g.stadium_raw))
    return out


def sweep(start: dt.date, end: dt.date, leagues=(1, 2), cache_dir: Path | None = None,
          pause_s: float = 0.15) -> list[Game]:
    """All games between two dates (inclusive) — used by the back-test."""
    out: list[Game] = []
    d = start
    while d <= end:
        for le in leagues:
            out.extend(fetch_games(d, le, cache_dir))
            if pause_s:
                time.sleep(pause_s)
        d += dt.timedelta(days=1)
    return out
