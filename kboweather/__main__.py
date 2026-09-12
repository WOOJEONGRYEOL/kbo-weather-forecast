"""CLI.

  python -m kboweather today [--league 1|2|all] [--html] [--telegram] [--narrate] [--speak]
  python -m kboweather date 2026-09-12 [...]
  python -m kboweather venue 잠실 --at 2026-09-12T17:00 [--league 1]
  python -m kboweather stadiums
  python -m kboweather backtest [--start 2026-03-20] [--end 2026-09-10]
  python -m kboweather verify
  python -m kboweather auto [--runner local|github] [--force]   # 예약 실행 (먼저 돈 쪽만 실행)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from . import backtest as backtest_mod
from . import forecast, narrate, notify, report, stadiums as stadiums_mod
from .config import load_settings
from .kbo import Game, games_on

KST = ZoneInfo("Asia/Seoul")


def _leagues(arg: str) -> tuple:
    return (1, 2) if arg == "all" else (int(arg),)


def cmd_day(date: dt.date, args) -> int:
    s = load_settings()
    stadiums = stadiums_mod.load()
    games = games_on(date, cache_dir=s.cache_dir)
    if args.venue:
        games = [g for g in games if stadiums_mod.resolve(g.stadium_raw, stadiums)
                 and stadiums_mod.resolve(g.stadium_raw, stadiums).key == stadiums_mod.find(args.venue, stadiums).key]
    day = forecast.run_day(date, games, stadiums, s, _leagues(args.league))
    return _emit(day, s, args)


def cmd_venue(args) -> int:
    s = load_settings()
    stadiums = stadiums_mod.load()
    st = stadiums_mod.find(args.venue, stadiums)
    if st is None:
        print(f"unknown venue: {args.venue}", file=sys.stderr)
        return 2
    at = dt.datetime.fromisoformat(args.at) if args.at else dt.datetime.now(KST).replace(tzinfo=None)
    g = Game(game_id="adhoc", date=at.strftime("%Y-%m-%d"), time=at.strftime("%H:%M"), league=int(args.league),
             stadium_raw=st.kbo_names[0] if st.kbo_names else st.short, home=st.short, away="(임의)",
             state="1", cancel="정상경기", series="임의조회")
    day = forecast.run_day(at.date(), [g], stadiums, s, (int(args.league),))
    return _emit(day, s, args)


def _emit(day: dict, s, args) -> int:
    out_dir: Path = s.out_dir
    stem = out_dir / day["date"]
    stem.with_suffix(".json").write_text(json.dumps(day, ensure_ascii=False, indent=1), "utf-8")
    md = report.to_markdown(day)
    stem.with_suffix(".md").write_text(md, "utf-8")
    html_path = None
    if getattr(args, "html", False) or True:
        html_path = stem.with_suffix(".html")
        html_path.write_text(report.to_html(day), "utf-8")
        (out_dir / "latest.html").write_text(report.to_html(day), "utf-8")
    text = md
    if getattr(args, "narrate", False):
        story = narrate.ollama(day, s.ollama_host, s.ollama_model)
        if story:
            (out_dir / f"{day['date']}-narrative.txt").write_text(story, "utf-8")
            text = story + "\n\n" + md
            print("🎙 " + story + "\n")
        else:
            print("(Ollama 서술 생성 실패 — 템플릿 브리핑만 사용)", file=sys.stderr)
    print(md)
    if getattr(args, "cards", False):
        from . import cards as cards_mod
        print("🖼 " + "  ".join(str(p) for p in cards_mod.make(day, day["reports"], out_dir)))
    if getattr(args, "speak", False):
        p = notify.speak(story if getattr(args, "narrate", False) and story else _speech_text(day), out_dir / f"{day['date']}-brief", s.tts_voice)
        print(f"🔊 {p}")
    if getattr(args, "telegram", False):
        if not (s.telegram_token and s.telegram_chat_id):
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없어 전송 생략", file=sys.stderr)
        else:
            from . import cards as cards_mod
            print("📨 " + cards_mod.send_briefing(s, day, story=story if getattr(args, "narrate", False) else None))
    print(f"\n→ {stem.with_suffix('.json')}  {stem.with_suffix('.md')}  {html_path}")
    return 0


def _speech_text(day: dict) -> str:
    lines = [f"{report.date_title(day['date'])} KBO 구장 기상 브리핑입니다."]
    for r in day["reports"]:
        rain = r["rain"]
        lines.append(f"{r['start'][11:16].replace(':', '시 ')}분 {r['stadium']['short']}, {r['label']}. {rain['verdict']}. "
                     f"경기 중 비 올 확률 {round(rain['p_rain']*100)}퍼센트, 취소 확률 {round(rain['p_cancel']*100)}퍼센트.")
    return " ".join(lines)


def cmd_stadiums(args) -> int:
    for st in stadiums_mod.load().values():
        print(f"{st.key:14s} {st.name:18s} ({st.lat:.5f},{st.lon:.5f}) tier{st.tier} {st.wind_note:14s} shelter {st.shelter} "
              f"fences L{st.fences.get('lf')}/C{st.fences.get('cf')}/R{st.fences.get('rf')} {st.surface}/{st.drainage} ← {', '.join(st.kbo_names)}")
    return 0


def cmd_backtest(args) -> int:
    s = load_settings()
    stadiums = stadiums_mod.load()
    games = backtest_mod.load_games(Path(args.games))
    summary = backtest_mod.run(games, stadiums, s.cache_dir, args.start, args.end, _leagues(args.league))
    print(backtest_mod.format_summary(summary))
    out = s.out_dir / f"backtest-{args.start}-{args.end}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    print(f"→ {out}")
    return 0


def cmd_verify(args) -> int:
    from . import verify as verify_mod
    s = load_settings()
    print(verify_mod.score(s.cache_dir.parent / "history" / "forecasts.jsonl", s.cache_dir))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kboweather", description="KBO 구장 핀포인트 기상 브리핑")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--league", default="all", choices=["1", "2", "all"])
        sp.add_argument("--venue", help="특정 구장만 (key/이름)")
        sp.add_argument("--html", action="store_true", help="(항상 생성) HTML 리포트")
        sp.add_argument("--telegram", action="store_true")
        sp.add_argument("--narrate", action="store_true", help="Ollama 로컬 LLM 캐스터 멘트")
        sp.add_argument("--speak", action="store_true", help="macOS say 로 음성 파일 생성")
        sp.add_argument("--cards", action="store_true", help="텔레그램용 카드 이미지(PNG)만 out/ 에 생성")

    sp = sub.add_parser("today"); common(sp)
    sp = sub.add_parser("date"); sp.add_argument("date"); common(sp)
    sp = sub.add_parser("venue"); sp.add_argument("venue"); sp.add_argument("--at"); sp.add_argument("--league", default="1", choices=["1", "2"])
    sp.add_argument("--narrate", action="store_true"); sp.add_argument("--speak", action="store_true"); sp.add_argument("--telegram", action="store_true")
    sub.add_parser("stadiums")
    sub.add_parser("verify", help="기상청 vs 앙상블 vs 최종 예측을 실제 취소 결과로 채점")
    sp = sub.add_parser("auto", help="예약 실행: 이 시간대를 아직 아무도 안 돌렸을 때만 실행")
    sp.add_argument("--runner", default="local", choices=["local", "github"])
    sp.add_argument("--force", action="store_true", help="표시 파일이 있어도 실행")
    sp.add_argument("--plan", action="store_true", help="실행할지(run)/건너뛸지(skip)만 출력")
    sp = sub.add_parser("backtest"); sp.add_argument("--start", default="2026-03-20"); sp.add_argument("--end", default="2026-09-10")
    sp.add_argument("--games", default=str(Path(__file__).resolve().parent.parent / "data" / "kbo_games_2026.json"))
    sp.add_argument("--league", default="all", choices=["1", "2", "all"])

    a = p.parse_args(argv)
    if a.cmd == "today":
        return cmd_day(dt.datetime.now(KST).date(), a)
    if a.cmd == "date":
        return cmd_day(dt.date.fromisoformat(a.date), a)
    if a.cmd == "venue":
        return cmd_venue(a)
    if a.cmd == "stadiums":
        return cmd_stadiums(a)
    if a.cmd == "backtest":
        return cmd_backtest(a)
    if a.cmd == "verify":
        return cmd_verify(a)
    if a.cmd == "auto":
        from . import auto as auto_mod
        return auto_mod.run(a.runner, a.force, a.plan)
    return 1


if __name__ == "__main__":
    sys.exit(main())
