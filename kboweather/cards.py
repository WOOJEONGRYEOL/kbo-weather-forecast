"""Telegram briefing as card images — the dashboard's look, sized for a phone.

One PNG per league (1군, 퓨처스), rendered by headless Chrome from a compact
card sheet and sent as one album with a short caption. If Chrome is missing or
rendering fails, the text version (one blockquote per game) goes out instead.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from . import notify, report

CHROME = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Chromium.app/Contents/MacOS/Chromium")
WIDTH, CARD_H, GAP = 720, 168, 10
FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700'
         '&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=block">')
CSS = """
:root{--bg:#EEF1EF;--surface:#FFFFFF;--surface-2:#F4F6F5;--ink:#14201B;--ink-2:#556661;--line:#D5DDD9;
--clay:#B4532B;--grass:#2F7A4E;--grass-2:#BFDFC9;--rain:#2B6CB0;--warn:#B8860B;--bad:#B23A3A;--ok:#2E7D4F;--heat:#C4451C;
--display:"Barlow Condensed","IBM Plex Sans KR","Apple SD Gothic Neo","Noto Sans CJK KR",sans-serif;
--body:"IBM Plex Sans KR","Apple SD Gothic Neo","Noto Sans CJK KR",sans-serif}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 var(--body);-webkit-font-smoothing:antialiased}
.sheet{width:720px;padding:22px 20px 0}
.head{display:flex;align-items:baseline;justify-content:space-between;height:34px;margin:0 2px 12px}
.head h1{font:700 34px/1 var(--display);margin:0;letter-spacing:.01em}
.head .lg{font:600 15px/1 var(--body);color:var(--ink-2)}
.sum{height:42px;overflow:hidden;font-size:14px;line-height:1.5;color:var(--ink-2);margin:0 2px 14px}
.card{display:grid;grid-template-columns:1fr 88px;column-gap:14px;height:CARD_Hpx;overflow:hidden;margin-bottom:GAPpx;
background:var(--surface);border:1px solid var(--line);border-left:6px solid var(--ok);border-radius:12px;padding:14px 16px 12px}
.card.warn{border-left-color:var(--warn)}.card.bad{border-left-color:var(--bad)}.card.none{border-left-color:var(--line)}
.top{display:flex;align-items:baseline;gap:10px;white-space:nowrap}
.time{font:700 30px/1 var(--display);font-variant-numeric:tabular-nums}
.venue{font:700 22px/1 var(--display)}
.tag{font:500 11px/1 var(--body);border:1px solid var(--ink-2);border-radius:3px;padding:3px 5px;color:var(--ink)}
.teams{font:500 15px/1 var(--body);color:var(--ink-2);margin-left:auto}
.verdict{margin-top:9px;font-weight:600;font-size:15px;white-space:nowrap}
.hot{color:var(--heat)}
.nums{display:grid;grid-template-columns:repeat(4,auto);justify-content:start;column-gap:24px;margin-top:7px}
.num .l{display:block;font-size:12px;color:var(--ink-2)}
.num .v{font:700 26px/1.1 var(--display);font-variant-numeric:tabular-nums}
.num .v.rain{color:var(--rain)}.num .v.up{color:var(--clay)}
.fine{margin-top:5px;font-size:12.5px;color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fig svg{width:88px;height:88px;display:block;margin-top:6px}
.foot{height:24px;font-size:11.5px;color:var(--ink-2);margin:4px 2px 0}
""".replace("CARD_H", str(CARD_H)).replace("GAP", str(GAP))


def _m(x: float) -> str:
    return report.signed(x, " m").replace("-", "−")


def _title(day: dict) -> str:
    d = dt.date.fromisoformat(day["date"])
    return f"{d.month}월 {d.day}일 ({report.WEEKDAYS[d.weekday()]})"


def _card(r: dict) -> str:
    e = html.escape
    g, st, rain, cond, carry, heat = r["game"], r["stadium"], r["rain"], r["conditions"], r["carry"], r["heat"]
    cf = next((x for x in carry["directions"] if x["direction"] == "CF"), {}) if carry else {}
    carry_v, hr_v, up = "—", "—", ""
    if carry and (st["dome"] or st["cf_azimuth"] is None or not cf):
        carry_v = _m(carry["air_only_delta_m"])
    elif carry:
        carry_v = _m(cf["delta_vs_ref_m"])
        hr = carry.get("hr_multiplier") or 1.0
        hr_v = f"{(hr - 1) * 100:+.0f}%".replace("-", "−") if abs(hr - 1) >= 0.005 else "±0%"
        up = "up" if cf["delta_vs_ref_m"] >= 2.0 else ""
    nums = [("경기 중 비", report.pct(rain["p_rain"]), "rain"), ("취소", report.pct(rain["p_cancel"]), ""),
            ("비거리" + (" · 돔" if st["dome"] else ""), carry_v, up), ("홈런", hr_v, "")]
    fine = []
    if cond:
        fine.append(f'{cond["time"][11:16]} {cond["temp"]:.0f}℃' if cond.get("temp") is not None else cond["time"][11:16])
        if st["dome"]:
            fine.append("돔 — 비·바람 영향 없음")
        elif cond.get("wind_speed") is not None:
            w = f'{cond["wind_compass"]}풍 {cond["wind_speed"]:.1f} m/s'
            if cf and st["cf_azimuth"] is not None:
                t = cf.get("tail_ms") or 0.0
                w += " · 외야 쪽 " + ("옆바람" if abs(t) < 0.3 else "뒷바람" if t > 0 else "맞바람")
            fine.append(w)
    if rain.get("kma"):
        fine.append(f'기상청 강수확률 {rain["kma"]["pop_max"]}%')
    verdict = e(rain["verdict"])
    if heat.get("level") not in (None, "ok", "unknown"):
        verdict += f' <span class="hot">· 체감 {heat["max_apparent"]}℃</span>'
    status = report.status_suffix(g).strip(" []")
    return (f'<div class="card {report._vclass(rain["verdict_icon"])}"><div class="main">'
            f'<div class="top"><span class="time">{e(r["start"][11:16])}</span><span class="venue">{e(st["short"])}</span>'
            + (f'<span class="tag">{e(status)}</span>' if status else "")
            + f'<span class="teams">{e(r["label"])}</span></div><div class="verdict">{verdict}</div><div class="nums">'
            + "".join(f'<div class="num"><span class="l">{k}</span><span class="v {c}">{v}</span></div>' for k, v, c in nums)
            + f'</div><div class="fine">{e(" · ".join(fine))}</div></div>'
            f'<div class="fig">{report.field_svg(st, cond, carry)}</div></div>')


def sheet(day: dict, rs: list[dict], name: str) -> tuple[str, int]:
    """A phone-width card sheet for one league, and the exact pixel height to screenshot."""
    e = html.escape
    summary = report.league_summary(rs).split(" · ", 1)[-1]
    body = (f'<div class="sheet"><div class="head"><h1>{e(_title(day))}</h1><span class="lg">KBO {e(name)} · {len(rs)}경기</span></div>'
            f'<p class="sum">{e(summary)}</p>' + "".join(_card(r) for r in rs)
            + f'<p class="foot">{e(day["generated_at"][11:16])} 예보 · 기상청 + 앙상블 212개 시나리오 · 비거리는 표준 타구 대비</p></div>')
    height = 22 + 34 + 12 + 42 + 14 + len(rs) * (CARD_H + 2 + GAP) + 28 + 12
    return f'<!doctype html><meta charset="utf-8">{FONTS}<style>{CSS}</style>{body}', height


def find_chrome() -> str | None:
    return next((p for c in CHROME if (p := shutil.which(c))), None)


def render(doc: str, out_png: Path, height: int, chrome: str, timeout: int = 40) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "cards.html"
        src.write_text(doc, "utf-8")
        out_png.unlink(missing_ok=True)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
               "--no-first-run", "--no-default-browser-check", "--disable-extensions",
               "--use-mock-keychain",            # macOS: never wait on a keychain prompt
               f"--user-data-dir={tmp}/profile", "--force-device-scale-factor=2",
               f"--window-size={WIDTH},{height}", "--virtual-time-budget=8000",
               f"--screenshot={out_png}", src.as_uri()]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)   # Chrome leaves helper processes; take the whole group
            proc.wait()
            raise RuntimeError(f"Chrome 이 {timeout}초 안에 끝나지 않음")
    if not out_png.exists() or out_png.stat().st_size < 2000:
        raise RuntimeError("카드 이미지가 만들어지지 않음")
    return out_png


def make(day: dict, reports: list[dict], out_dir: Path) -> list[Path]:
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Chrome 을 찾을 수 없음")
    pngs = []
    for le, name, _ in report.LEAGUES:
        rs = [r for r in reports if r["game"]["league"] == le]
        if rs:
            doc, h = sheet(day, rs, name)
            pngs.append(render(doc, (out_dir / f"{day['date']}-cards-{le}.png").resolve(), h, chrome))
    return pngs


def caption(day: dict, reports: list[dict]) -> str:
    lines = [f"⚾ <b>{html.escape(_title(day))} KBO 구장 날씨</b>"]
    for le, name, _ in report.LEAGUES:
        rs = [r for r in reports if r["game"]["league"] == le]
        if rs:
            lines.append(f"{name} {len(rs)}경기 — {html.escape(report.league_summary(rs).split(' · ', 1)[-1])}")
    return "\n".join(lines)


def send_briefing(s, day: dict, reports: list[dict] | None = None, story: str | None = None) -> str:
    """Cards first; the text version if images can't be made or sent."""
    reports = day["reports"] if reports is None else reports
    if not reports:
        return "보낼 경기 없음"
    cap = caption(day, reports)
    if story:
        cap = "🎙 " + html.escape(story[:500]) + "\n\n" + cap
    try:
        pngs = make(day, reports, s.out_dir)
        notify.telegram_photos(s.telegram_token, s.telegram_chat_id, pngs, cap[:1024])
        return f"텔레그램 카드 {len(pngs)}장 전송"
    except Exception as e:  # never lose the briefing over a rendering problem
        notify.telegram(s.telegram_token, s.telegram_chat_id, report.to_telegram_html({**day, "reports": reports}))
        return f"텔레그램 텍스트 전송 (카드 실패: {str(e).splitlines()[0][:80]})"
