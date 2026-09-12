"""Scheduled runs — the Mac first, GitHub Actions as the backup.

Each day has two slots: am (퓨처스 11:00/13:00, weekend 1군 day games) and pm
(1군 night games). Whoever runs a slot first leaves a marker in data/runs/; the
other side sees it after `git pull` and skips — so Telegram never gets the same
briefing twice and the forecast history stays in one place.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from . import cards, forecast, report, stadiums as stadiums_mod
from .config import load_settings
from .kbo import fetch_games, games_on

KST = ZoneInfo("Asia/Seoul")
RUNS = Path(__file__).resolve().parent.parent / "data" / "runs"


PRE_MIN, PRE_MAX = 50, 70     # 1군 경기 시작 50~70분 전 사이에 한 번 (15분마다 깨우면 반드시 걸림)
PM_GAP = 75                   # 경기 전 실행과 이만큼 붙으면 오후 정기 실행은 생략


def first_pitches(now: dt.datetime, cache_dir) -> list[dt.datetime]:
    """오늘 1군 경기 시작 시각들. 그날 일정을 그대로 읽으므로 시간이 바뀌면 따라간다."""
    try:
        # 시작 시각은 한 번 정해지면 바뀌지 않으므로 하루 한두 번만 받아오고 캐시를 재사용한다
        games = fetch_games(now.date(), 1, cache_dir, cache_ttl_s=6 * 3600)
    except Exception:
        return []                 # 일정 조회 실패 시엔 정기 슬롯만
    out = set()
    for g in games:
        try:
            out.add(dt.datetime.combine(now.date(), dt.time.fromisoformat(g.time)))
        except ValueError:
            continue
    return sorted(out)


def slot_for(now: dt.datetime, starts: list[dt.datetime] | None = None) -> str | None:
    """pre-HHMM: 1군 경기 시작 1시간 전 · am: 아침(06–12시) · pm: 오후(12–21시)."""
    for start in starts or []:
        if PRE_MIN <= (start - now).total_seconds() / 60 <= PRE_MAX:
            return f"pre-{start:%H%M}"
    if 6 <= now.hour < 12:
        return "am"
    if 12 <= now.hour < 21:
        gap = min((abs((s - dt.timedelta(minutes=60) - now).total_seconds()) / 60 for s in starts or []), default=None)
        if gap is not None and gap <= PM_GAP:
            return None           # 곧(또는 방금) 경기 전 실행이 있으므로 정기 실행은 건너뜀
        return "pm"
    return None


def run(runner: str, force: bool = False, plan: bool = False, now: dt.datetime | None = None) -> int:
    """plan=True prints only "run" or "skip" (for the workflow to gate its heavier steps)."""
    now = now or dt.datetime.now(KST).replace(tzinfo=None)
    s = load_settings()
    starts = first_pitches(now, s.cache_dir) if 9 <= now.hour < 22 else []
    slot = slot_for(now, starts)
    marker = RUNS / f"{now:%Y-%m-%d}-{slot or 'pm'}.json"
    why = None
    if slot is None and not force:
        why = f"{now:%H:%M} — 브리핑 시간대(06–21시)가 아니라 건너뜀"
    elif marker.exists() and not force:
        done = json.loads(marker.read_text("utf-8"))
        why = f"{marker.name}: {done.get('runner')}에서 {done.get('at', '')[11:16]}에 이미 실행 — 건너뜀"
    if plan:
        print("skip" if why else "run")
        return 0
    if why:
        print(why)
        return 0

    day = forecast.run_day(now.date(), games_on(now.date(), cache_dir=s.cache_dir), stadiums_mod.load(), s, (1, 2))
    s.out_dir.mkdir(parents=True, exist_ok=True)
    stem = s.out_dir / day["date"]
    stem.with_suffix(".json").write_text(json.dumps(day, ensure_ascii=False, indent=1), "utf-8")
    stem.with_suffix(".md").write_text(report.to_markdown(day), "utf-8")
    stem.with_suffix(".html").write_text(report.to_html(day), "utf-8")
    (s.out_dir / "latest.html").write_text(report.to_html(day), "utf-8")

    upcoming = [r for r in day["reports"] if (r["rain"].get("lead_h") or 0) > 0]   # message: games not yet started
    if not (s.telegram_token and s.telegram_chat_id):
        sent = "텔레그램 미설정"
    elif not upcoming:
        sent = "남은 경기 없음"
    else:
        sent = cards.send_briefing(s, day, upcoming)
    RUNS.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"runner": runner, "at": day["generated_at"], "games": len(day["reports"]),
                                  "upcoming": len(upcoming), "delivery": sent}, ensure_ascii=False), "utf-8")
    kma_ok = sum(1 for r in day["reports"] if r["sources"].get("kma"))
    print(f"{marker.name}: {runner} 실행 — {len(day['reports'])}경기 계산(기상청 연결 {kma_ok}구장), "
          f"남은 경기 {len(upcoming)}, {sent}")
    for w in sorted({w for r in day["reports"] for w in r["sources"].get("warnings", [])})[:3]:
        print(f"  경고: {w}")
    return 0
