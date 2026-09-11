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

from . import forecast, notify, report, stadiums as stadiums_mod
from .config import load_settings
from .kbo import games_on

KST = ZoneInfo("Asia/Seoul")
RUNS = Path(__file__).resolve().parent.parent / "data" / "runs"


def slot_for(now: dt.datetime) -> str | None:
    """am 06–15시, pm 15–21시. Outside that there is no game left to brief."""
    if 6 <= now.hour < 15:
        return "am"
    if 15 <= now.hour < 21:
        return "pm"
    return None


def run(runner: str, force: bool = False, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(KST).replace(tzinfo=None)
    slot = slot_for(now)
    if slot is None and not force:
        print(f"{now:%H:%M} — 브리핑 시간대(06–21시)가 아니라 건너뜀")
        return 0
    marker = RUNS / f"{now:%Y-%m-%d}-{slot or 'pm'}.json"
    if marker.exists() and not force:
        done = json.loads(marker.read_text("utf-8"))
        print(f"{marker.name}: {done.get('runner')}에서 {done.get('at', '')[11:16]}에 이미 실행 — 건너뜀")
        return 0

    s = load_settings()
    day = forecast.run_day(now.date(), games_on(now.date(), cache_dir=s.cache_dir), stadiums_mod.load(), s, (1, 2))
    s.out_dir.mkdir(parents=True, exist_ok=True)
    stem = s.out_dir / day["date"]
    stem.with_suffix(".json").write_text(json.dumps(day, ensure_ascii=False, indent=1), "utf-8")
    stem.with_suffix(".md").write_text(report.to_markdown(day), "utf-8")
    stem.with_suffix(".html").write_text(report.to_html(day), "utf-8")

    upcoming = [r for r in day["reports"] if (r["rain"].get("lead_h") or 0) > 0]   # message: games not yet started
    if not (s.telegram_token and s.telegram_chat_id):
        sent = "텔레그램 미설정"
    elif not upcoming:
        sent = "남은 경기 없음"
    else:
        notify.telegram(s.telegram_token, s.telegram_chat_id, report.to_telegram_html({**day, "reports": upcoming}))
        sent = "텔레그램 전송"
    RUNS.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"runner": runner, "at": day["generated_at"], "games": len(day["reports"]),
                                  "upcoming": len(upcoming), "delivery": sent}, ensure_ascii=False), "utf-8")
    print(f"{marker.name}: {runner} 실행 — {len(day['reports'])}경기 계산, 남은 경기 {len(upcoming)}, {sent}")
    return 0
