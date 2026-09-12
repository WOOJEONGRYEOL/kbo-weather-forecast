"""Render a day report as Markdown (Telegram/console) and a self-contained HTML dashboard."""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import math
from functools import lru_cache
from pathlib import Path

from .explain import explain

WEEKDAYS = "월화수목금토일"


def pct(x) -> str:
    return "-" if x is None else f"{round(x * 100):d}%"


def signed(x, unit="m") -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    x = 0.0 if abs(x) < 0.05 else x           # no "-0.0"
    return f"{x:+.1f}{unit}".replace("-", "−")


def status_suffix(g: dict) -> str:
    """KBO's own state for the game: 종료 / 진행중 / 실제 취소 사유."""
    if g.get("cancel") and g["cancel"] != "정상경기":
        return f" [KBO 공식: {g['cancel']}]"
    return {"3": " [종료]", "2": " [진행중]"}.get(str(g.get("state")), "")


def date_title(date: str) -> str:
    d = dt.date.fromisoformat(date)
    return f"{d:%Y-%m-%d} ({WEEKDAYS[d.weekday()]})"


# ---- Markdown / Telegram ------------------------------------------------------
def game_lines(r: dict) -> list[str]:
    g, st, rain, cond, carry, heat = r["game"], r["stadium"], r["rain"], r["conditions"], r["carry"], r["heat"]
    t = r["start"][11:16]
    lines = [f"{rain['verdict_icon']} {t} {st['short']} · {r['label']} — {rain['verdict']}{status_suffix(g)}"]
    q = rain.get("game_mm") or {}
    lines.append(f"  강수 확률: 경기중 {pct(rain['p_rain'])} / 중단 {pct(rain['p_delay'])} / 취소 {pct(rain['p_cancel'])}"
                 f" · 경기 시간대 누적 {q.get('p50', 0)}mm (상위 10% 시나리오 {q.get('p90', 0)}mm)"
                 f" · 앙상블 {rain['members']}멤버+모델 {len(rain['det'])}개"
                 + (f" · 기상청 강수확률 최고 {rain['kma']['pop_max']}%" if rain.get("kma") else ""))
    if cond:
        w = f"{cond['wind_compass']}풍 {cond['wind_speed']}m/s" if cond.get("wind_speed") is not None else "바람 -"
        lines.append(f"  {cond['time'][11:16]} 기온 {cond['temp']}℃ · 습도 {cond['rh']}% · {w}"
                     f" · 구름 {cond['cloud']}% · 기압 {cond['pressure_msl']}hPa")
    if carry:
        dirs = {d["direction"]: d for d in carry["directions"]}
        cf = dirs["CF"]
        if st["dome"]:
            lines.append(f"  비거리 지수(돔·공기밀도만): 표준 대비 {signed(carry['air_only_delta_m'])}")
        elif st["cf_azimuth"] is None:
            lines.append(f"  비거리 지수(공기밀도만, 방위각 미확정): {signed(carry['air_only_delta_m'])}")
        else:
            lines.append(f"  비거리 지수: 중앙 {signed(cf['delta_vs_ref_m'])} (좌 {signed(dirs['LF']['delta_vs_ref_m'])} / 우 {signed(dirs['RF']['delta_vs_ref_m'])})"
                         f" · 외야 방향 바람 {signed(cf['tail_ms'], 'm/s')} · 홈런 기대 ×{carry['hr_multiplier']:.2f}")
    if heat.get("level") not in (None, "ok", "unknown"):
        lines.append(f"  🌡 {heat['text']} (최고 체감 {heat['max_apparent']}℃ @ {heat['at'][11:16]})")
    return lines


def to_markdown(day: dict) -> str:
    out = [f"⚾ KBO 직관 날씨 예보 — {date_title(day['date'])}", ""]
    for league, name in ((1, "1군"), (2, "퓨처스")):
        rs = [r for r in day["reports"] if r["game"]["league"] == league]
        if not rs:
            continue
        out.append(f"[{name}] {len(rs)}경기")
        for r in rs:
            out.extend(game_lines(r))
            out.append("")
    if day.get("skipped"):
        out.append("건너뜀: " + "; ".join(f"{s['game'].get('stadium_raw')} {s['reason']}" for s in day["skipped"]))
    kma = "기상청 단기·초단기예보 + " if any(r["rain"].get("kma") for r in day["reports"]) else ""
    out.append(f"생성 {day['generated_at'][:16]} · 데이터 {kma}Open-Meteo(ECMWF/GFS/ICON/UKMO/GEM/JMA 앙상블) + KBO 일정")
    return "\n".join(out)


def to_telegram_html(day: dict) -> str:
    """Text fallback when card images can't be made: one <blockquote> box per game."""
    e = html.escape
    d = dt.date.fromisoformat(day["date"])
    out = [f"⚾ <b>KBO 직관 날씨 예보 · {d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})</b>", f"<i>{e(data_stamp(day))}</i>"]
    for le, name, _ in LEAGUES:
        rs = [r for r in day["reports"] if r["game"]["league"] == le]
        if not rs:
            continue
        out.append(f"\n<b>{name}</b> · {e(league_summary(rs))}")
        for r in rs:
            st, rain, cond, carry = r["stadium"], r["rain"], r["conditions"], r["carry"]
            box = [f"<b>{e(r['start'][11:16])} {e(st['short'])}</b> · {e(r['label'])}{e(status_suffix(r['game']))}",
                   f"{rain['verdict_icon']} {e(rain['verdict'])}",
                   f"☔ 강수 {pct(rain['p_rain'])} · 취소 {pct(rain['p_cancel'])}"]
            cf = next((x for x in carry["directions"] if x["direction"] == "CF"), None) if carry else None
            if carry and (st["dome"] or st["cf_azimuth"] is None or not cf):
                box.append(f"⚾ 비거리 {signed(carry['air_only_delta_m'], ' m')}" + (" (돔)" if st["dome"] else ""))
            elif carry:
                box.append(f"⚾ 비거리 {signed(cf['delta_vs_ref_m'], ' m')} · 홈런 ×{carry['hr_multiplier']:.2f}")
            if cond and cond.get("wind_speed") is not None:
                box.append(f"🌬 {e(cond['wind_compass'])}풍 {cond['wind_speed']:.1f} m/s · {cond['temp']}℃")
            if r["heat"].get("level") not in (None, "ok", "unknown"):
                box.append(f"🌡 {e(r['heat']['text'])}")
            out.append("<blockquote>" + "\n".join(box) + "</blockquote>")
    return "\n".join(out)


# ---- HTML dashboard ------------------------------------------------------------
FONT_LINK = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
             'family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Sans+KR:wght@400;500;600&display=swap">')

CSS = """
:root{--bg:#EEF1EF;--surface:#FFFFFF;--surface-2:#F4F6F5;--ink:#14201B;--ink-2:#556661;--line:#D5DDD9;
--clay:#B4532B;--grass:#2F7A4E;--grass-2:#BFDFC9;--rain:#2B6CB0;--warn:#B8860B;--bad:#B23A3A;--ok:#2E7D4F;--heat:#C4451C;
--display:"Barlow Condensed","IBM Plex Sans KR","Apple SD Gothic Neo","Noto Sans KR",sans-serif;
--body:"IBM Plex Sans KR","Apple SD Gothic Neo","Noto Sans KR",sans-serif}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0F1614;--surface:#182220;--surface-2:#1F2B28;--ink:#E6ECE9;--ink-2:#9FB0A8;--line:#2B3936;
--clay:#DB7E52;--grass:#4FA774;--grass-2:#27503A;--rain:#6FA3E6;--warn:#D9A441;--bad:#E06A6A;--ok:#5DBB86;--heat:#F07A4E}}
:root[data-theme=dark]{--bg:#0F1614;--surface:#182220;--surface-2:#1F2B28;--ink:#E6ECE9;--ink-2:#9FB0A8;--line:#2B3936;
--clay:#DB7E52;--grass:#4FA774;--grass-2:#27503A;--rain:#6FA3E6;--warn:#D9A441;--bad:#E06A6A;--ok:#5DBB86;--heat:#F07A4E}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--body);word-break:keep-all}
main{max-width:1040px;margin:0 auto;padding:28px 20px 56px}
.top{padding-bottom:2px}
.eyebrow{font:600 12px/1 var(--body);letter-spacing:.12em;text-transform:uppercase;color:var(--ink-2);margin-bottom:8px}
h1{font:700 42px/1 var(--display);margin:0;letter-spacing:.01em;text-wrap:balance}
.summary{color:var(--ink-2);margin:0 0 14px;max-width:80ch}
.stamp{color:var(--ink-2);font-size:13px;margin:10px 0 0}
h2{font:600 24px/1.1 var(--display);margin:34px 0 12px;letter-spacing:.02em;display:flex;align-items:baseline;gap:10px}
h2 small{font:500 13px var(--body);color:var(--ink-2)}
.game{display:grid;grid-template-columns:minmax(150px,1fr) minmax(250px,1.7fr) minmax(220px,1.1fr);gap:12px 26px;
background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:16px 18px;margin:0 0 12px}
.game>.rain{grid-column:2/-1}
.game>details{grid-column:1/-1}
.carrybox{grid-column:1/-1;border-top:1px dashed var(--line);padding-top:12px;display:grid;
grid-template-columns:1fr;gap:12px;align-items:center}
.carrybox .flight{grid-column:auto;border-top:0;margin-top:0;padding-top:0}
@media(max-width:760px){.carrybox{grid-template-columns:1fr}}
@media(max-width:760px){.game{grid-template-columns:1fr}}
.time{font:700 36px/1 var(--display);font-variant-numeric:tabular-nums}
.venue{font:600 19px/1.2 var(--display);margin-top:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tag{font:500 11px/1 var(--body);letter-spacing:.08em;border:1px solid var(--line);border-radius:3px;padding:3px 6px;color:var(--ink-2);white-space:nowrap}
.tag.kbo{color:var(--ink);border-color:var(--ink-2)}
.teams{margin-top:7px;font-weight:500;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tm[class*="tm-"]{background-color:transparent;box-shadow:none;font-size:0;background-size:contain;background-repeat:no-repeat;background-position:center}
.tm{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:50%;flex:none;
font:700 10.5px/1 var(--body);letter-spacing:-.03em;color:#fff;background:var(--tm-bg,#555);
box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.22)}
.meta{color:var(--ink-2);font-size:12.5px;margin-top:2px}
.verdict{font-weight:600;display:flex;align-items:center;gap:8px}
.verdict::before{content:"";flex:none;width:10px;height:10px;border-radius:50%;background:var(--ok)}
.verdict.warn::before{background:var(--warn)}.verdict.bad::before{background:var(--bad)}.verdict.none::before{background:var(--line)}
.meters{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:10px}
.meter{display:grid;grid-template-columns:1fr auto;align-items:baseline;row-gap:5px}
.meter .lbl{font-size:12px;color:var(--ink-2)}
.meter .val{font:700 27px/1 var(--display);font-variant-numeric:tabular-nums}
.meter i{grid-column:1/-1;display:block;height:6px;background:var(--surface-2);border-radius:3px;position:relative;overflow:hidden}
.meter i::after{content:"";position:absolute;inset:0;width:var(--w);background:var(--rain);border-radius:3px}
.fine{font-size:12px;color:var(--ink-2);margin-top:8px}
.field{display:grid;grid-template-columns:100px 1fr;grid-template-rows:auto auto auto;gap:3px 12px;align-items:center}
.field svg{width:100px;height:100px;grid-row:1/4;display:block}
.carry .big{font:700 28px/1 var(--display);font-variant-numeric:tabular-nums}
.carry .big.up{color:var(--clay)}
.carry .sub,.wind,.air{font-size:12px;color:var(--ink-2)}
.wind b{color:var(--ink);font-weight:500}
.hot{color:var(--heat);font-weight:600}
details{border-top:1px dashed var(--line);padding-top:8px;font-size:13px;color:var(--ink-2)}
summary{cursor:pointer;color:var(--ink);font-weight:500;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸ "}details[open] summary::before{content:"▾ "}
summary:focus-visible{outline:2px solid var(--rain);outline-offset:2px}
.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:6px;font-variant-numeric:tabular-nums}
th,td{padding:4px 8px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--ink-2);font-weight:500;font-size:11.5px;letter-spacing:.04em}
td:first-child,th:first-child{text-align:left}
td.wet{color:var(--rain);font-weight:500}
.legend{margin-top:36px;font-size:12.5px;color:var(--ink-2);line-height:1.65;max-width:78ch;border-top:1px solid var(--line);padding-top:14px}
.legend b{color:var(--ink);font-weight:500}
ul.skip{color:var(--ink-2);font-size:13px;overflow-wrap:anywhere}
[hidden]{display:none!important}
.tabs{display:flex;gap:22px;margin-top:18px;border-bottom:2px solid var(--ink)}
.tab{appearance:none;background:none;border:0;border-bottom:4px solid transparent;margin-bottom:-2px;padding:6px 2px 8px;
font:600 24px/1 var(--display);letter-spacing:.02em;color:var(--ink-2);cursor:pointer;display:flex;align-items:baseline;gap:8px}
.tab small{font:500 12px/1 var(--body);letter-spacing:0;color:var(--ink-2)}
.tab:hover{color:var(--ink)}
.tab[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--clay)}
.tab:focus-visible{outline:2px solid var(--rain);outline-offset:3px;border-radius:3px}
.panel{padding-top:16px}
.empty{color:var(--ink-2);padding:28px 0}
.why-body{display:grid;gap:10px;margin-top:10px;max-width:70ch}
.why-body p{margin:0;color:var(--ink);font-size:14px;line-height:1.75}
.why-body b{font-weight:600;margin-right:8px}
.why-body p.note{color:var(--ink-2);font-size:12.5px;line-height:1.65}
/* ---- 움직임: 바람 흐름, 타구 궤적, 시간대별 재생 ---- */
@keyframes drift{0%{transform:translateX(-46px);opacity:0}20%{opacity:.5}80%{opacity:.5}100%{transform:translateX(46px);opacity:0}}
@keyframes fly{0%{offset-distance:0%}70%{offset-distance:100%}100%{offset-distance:100%}}
@keyframes rainfall{to{background-position:0 46px}}
.streak{stroke:var(--rain);stroke-width:2;stroke-linecap:round;opacity:0;animation:drift var(--dur,3s) linear infinite var(--delay,0s)}
.flight{grid-column:1/-1;border-top:1px dashed var(--line);margin-top:2px;padding-top:10px;display:grid;
grid-template-columns:1fr 136px;grid-template-rows:auto auto;gap:4px 14px;align-items:center}
.flight .lab{grid-column:2;grid-row:1/3}
.flight .full{aspect-ratio:880/148}
.flight .zoom{aspect-ratio:880/188;background:var(--surface-2);border-radius:8px}
.flight .mark{stroke:var(--ink-2);stroke-width:2}.flight .mark.today{stroke:var(--clay)}
.flight .mlab{font:600 12px var(--body);fill:var(--ink-2)}.flight .mlab.today{fill:var(--clay)}
.flight .lab .note{display:block;margin-top:6px;font-size:11px;color:var(--ink-2)}
.flight svg{width:100%;height:auto;display:block}
.flight .trace{fill:none;stroke:var(--ink-2);stroke-width:1.3;stroke-dasharray:4 4;opacity:.75}
.flight .trace.today{stroke:var(--clay);stroke-width:2.2;stroke-dasharray:none;opacity:1}
.ball{animation:fly var(--dur,5s) linear infinite;offset-path:var(--p);offset-rotate:0deg}
.ball.ghost{opacity:.6}
.ball.ghost circle{stroke-dasharray:3.5 3}
.flight .lab{font-size:12px;color:var(--ink-2);line-height:1.6}
.flight .lab b{display:block;font:700 17px/1.2 var(--display);color:var(--ink);margin-bottom:2px}
.flight .k{display:inline-block;width:12px;height:0;border-top:2px solid var(--ink-2);vertical-align:middle;margin-right:6px}
.flight .k.today{border-top-color:var(--clay);border-top-width:3px}
.play{display:grid;grid-template-columns:200px 1fr;gap:14px;align-items:center;margin:10px 0 12px}
.play .stage{position:relative;height:168px;display:grid;place-items:center;overflow:hidden;border-radius:8px;background:linear-gradient(160deg,var(--surface-2),var(--surface))}
.play .plane{width:188px;height:188px;transform:perspective(560px) rotateX(50deg);transform-origin:50% 50%}
.play .plane svg{width:188px;height:188px;display:block}
.play .windrose{transform:rotate(var(--to,0deg));transform-origin:50px 50px;transition:transform .12s linear}
.pnote{font-size:11.5px;color:var(--ink-2);margin-top:2px}
.play .rain{position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity .5s;
background-image:repeating-linear-gradient(74deg,var(--rain) 0 1px,transparent 1px 9px);animation:rainfall .8s linear infinite}
.play .controls{display:grid;gap:9px;justify-items:start}
.pbtn{appearance:none;border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:999px;
padding:7px 16px;font:600 13px var(--body);cursor:pointer}
.pbtn:hover{border-color:var(--ink-2)}
.pbtn:focus-visible,.scrub:focus-visible{outline:2px solid var(--rain);outline-offset:2px}
.scrub{width:100%;accent-color:var(--grass)}
.rvals{font-size:12.5px;color:var(--ink-2);font-variant-numeric:tabular-nums;line-height:1.6}
.rvals b{color:var(--ink);font-weight:600}
@media(max-width:760px){.flight{grid-template-columns:1fr}.play{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.streak,.ball,.play .rain{animation:none}}
.static .streak,.static .ball,.static .play .rain{animation:none}
"""


def field_svg(st: dict, cond: dict, carry: dict | None, streaks: bool = False) -> str:
    """North-up plan of the park: fan rotated to its true center-field bearing,
    wind arrow (direction the wind blows TO) scaled by speed."""
    az = st.get("cf_azimuth")
    dome = st.get("dome")
    hx, hy, r = 50.0, 74.0, 46.0          # home plate + fence radius in a 100×100 box
    lx, ly = hx - r * math.sin(math.pi / 4), hy - r * math.cos(math.pi / 4)
    rx, ry = hx + r * math.sin(math.pi / 4), ly
    fan = f"M{hx:.1f},{hy:.1f} L{lx:.1f},{ly:.1f} A{r},{r} 0 0 1 {rx:.1f},{ry:.1f} Z"
    diamond = f"M{hx:.1f},{hy:.1f} l-11,-11 l11,-11 l11,11 z"
    rot = f' transform="rotate({az:.0f},50,50)"' if az is not None else ""
    parts = [f'<svg viewBox="0 0 100 100" role="img" aria-label="{html.escape(st["name"])} 외야 방향과 바람">',
             f'<g{rot} opacity="{0.55 if dome or az is None else 1}">'
             f'<path d="{fan}" fill="var(--grass-2)" stroke="var(--grass)" stroke-width="1.5" stroke-linejoin="round"/>'
             f'<path d="{diamond}" fill="var(--surface)" stroke="var(--grass)" stroke-width="1"/></g>',
             '<text x="50" y="9" text-anchor="middle" font-size="8" font-weight="600" fill="var(--ink-2)" font-family="var(--body)">N</text>']
    if dome:
        parts.append('<circle cx="50" cy="50" r="44" fill="none" stroke="var(--ink-2)" stroke-width="1" stroke-dasharray="3 3"/>'
                     '<text x="50" y="96" text-anchor="middle" font-size="8" fill="var(--ink-2)" font-family="var(--body)">돔</text>')
    elif cond and cond.get("wind_speed") is not None and cond.get("wind_dir") is not None and az is not None:
        spd = float(cond["wind_speed"])
        to = math.radians((float(cond["wind_dir"]) + 180.0) % 360.0)
        length = min(44.0, 6.0 + 7.0 * spd)
        dx, dy = math.sin(to), -math.cos(to)
        x1, y1 = 50 - dx * length / 2, 50 - dy * length / 2
        x2, y2 = 50 + dx * length / 2, 50 + dy * length / 2
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="var(--clay)" stroke-width="3" stroke-linecap="round"/>'
                     f'<polygon points="0,-5 9,0 0,5" fill="var(--clay)" transform="translate({x2:.1f},{y2:.1f}) rotate({math.degrees(math.atan2(dy, dx)):.0f})"/>')
        parts.append(wind_streaks(math.degrees(math.atan2(dy, dx)), spd))
    if streaks and not dome:
        parts.append(wind_streaks(0.0, 3.0))     # 방향·속도는 재생 스크립트가 CSS 변수로 바꿈
    parts.append("</svg>")
    return "".join(parts)


def wind_streaks(angle_deg: float, speed_ms: float, cls: str = "") -> str:
    """Short lines drifting the way the air moves — faster wind, faster drift."""
    dur = max(1.1, min(6.0, 14.0 / max(speed_ms, 0.3)))
    lines = "".join(f'<line class="streak" x1="43" y1="{y}" x2="57" y2="{y}" style="--dur:{dur:.1f}s;--delay:{d}s"/>'
                    for y, d in ((36, 0.0), (50, 0.5), (64, 1.0)))
    return f'<g class="windrose {cls}" transform="rotate({angle_deg:.0f},50,50)">{lines}</g>'


def flight_svg(r: dict) -> str:
    """Two views of the same flight: the whole arc, and a close-up of the fence.
    Both panels draw the same path at different scales, so the two balls (today's
    and the reference atmosphere's) stay in step and split exactly where the
    weather splits them."""
    e, carry, st = html.escape, r["carry"], r["stadium"]
    if not carry or not carry.get("path_ref"):
        return ""
    ref, today = carry["path_ref"], carry.get("path_cf") or carry["path_ref"]
    cf = next((d for d in carry["directions"] if d["direction"] == "CF"), None)
    fence = float((st.get("fences") or {}).get("cf") or 0) or None
    cycle = ((cf or {}).get("hang_time_s") or carry.get("ref_hang_s") or 5.0) * 1.5
    land_t, land_r = today[-1][0], ref[-1][0]
    max_x = max(land_t, land_r, (fence or 0) + 4)
    max_z = max([p[1] for p in ref] + [p[1] for p in today]) or 1.0

    def panel(w, base, top, pad, x0, x1, zmax, cls, extra=""):
        sx, sz = (w - pad * 2) / (x1 - x0), (base - top) / zmax

        def d_of(pts):
            return "M" + " L".join(f"{pad + (x - x0) * sx:.1f},{base - z * sz:.1f}" for x, z in pts)

        def ball(path_d, ghost):
            seam = "#9AA3A0" if ghost else "#C0392B"
            fill, line = ("#FFFFFF", "#8A948F") if ghost else ("#FBFBF8", "#14201B")
            return (f'<g class="ball{" ghost" if ghost else ""}" style="--p:path(\'{path_d}\');--dur:{cycle:.1f}s">'
                    f'<circle r="13" fill="{fill}" stroke="{line}" stroke-width="2.2"/>'
                    f'<path d="M-7,-10.2 A13.7,13.7 0 0 0 -7,10.2" fill="none" stroke="{seam}" stroke-width="2.6"/>'
                    f'<path d="M7,-10.2 A13.7,13.7 0 0 1 7,10.2" fill="none" stroke="{seam}" stroke-width="2.6"/></g>')

        out = [f'<svg class="{cls}" viewBox="0 0 {w:.0f} {base + 20:.0f}" role="img" aria-hidden="true">',
               f'<line x1="0" y1="{base}" x2="{w:.0f}" y2="{base}" stroke="var(--line)" stroke-width="1.5"/>', extra]
        if fence:
            fx = pad + (fence - x0) * sx
            fh = min(base - top, 2.4 * sz)          # 담장 높이는 자료가 없어 표시용 2.4 m
            out.append(f'<rect x="{fx - 3:.1f}" y="{base - fh:.1f}" width="6" height="{fh:.1f}" fill="var(--grass)" rx="1.5"/>')
        out.append(f'<path class="trace" d="{d_of(ref)}"/><path class="trace today" d="{d_of(today)}"/>')
        out.append(ball(d_of(ref), True) + ball(d_of(today), False) + "</svg>")
        return "".join(out), sx, pad

    full, _, _ = panel(880, 128, 16, 14, 0.0, max_x, max_z, "full")
    x0 = max(0.0, (fence or max(land_t, land_r)) - 20.0)
    x1 = max(land_t, land_r, (fence or 0)) + 5.0
    zmax = min(max([z for x, z in today + ref if x >= x0] + [4.0]), 8.0)   # 담장이 눌리지 않게 상한
    marks = []
    zsx = (880 - 28) / (x1 - x0)
    for x, label, cls, ly in ((land_t, f"오늘 {land_t:g} m", "today", 164), (land_r, f"표준 {land_r:g} m", "", 180)):
        mx = 14 + (x - x0) * zsx
        anchor = "end" if mx > 800 else "middle"
        marks.append(f'<line class="mark {cls}" x1="{mx:.1f}" y1="148" x2="{mx:.1f}" y2="138"/>'
                     f'<text class="mlab {cls}" x="{mx:.1f}" y="{ly}" text-anchor="{anchor}">{label}</text>')
    if fence:
        fx = 14 + (fence - x0) * zsx
        marks.append(f'<text class="mlab" x="{fx:.1f}" y="{22}" text-anchor="middle">중앙담장 {fence:g} m</text>')
    zoom, _, _ = panel(880, 148, 26, 14, x0, x1, zmax, "zoom", "".join(marks))
    delta = (cf or {}).get("delta_vs_ref_m") or carry.get("air_only_delta_m") or 0.0
    verdict = ("돔 — 공기 무게만 반영" if st["dome"] else
               "바람이 밀어줌" if delta > 0.5 else "바람이 붙잡음" if delta < -0.5 else "바람 영향 작음")
    lab = (f'<div class="lab"><b>{signed(delta)}</b>'
           f'<span class="k today"></span>오늘 {land_t:g} m<br>'
           f'<span class="k"></span>표준 {land_r:g} m<br>{e(verdict)}'
           f'<span class="note">아래는 담장 앞 {int(x1 - x0)} m 확대</span></div>')
    return f'<div class="flight">{full}{zoom}{lab}</div>'



LOGO_DIR = Path(__file__).resolve().parent.parent / "data" / "logos"
LOGO_CODES = {"LG": "LG", "두산": "OB", "KT": "KT", "KIA": "HT", "삼성": "SS", "롯데": "LT",
              "한화": "HH", "NC": "NC", "키움": "WO", "SSG": "SK"}


def logo_code(name: str | None) -> str | None:
    if not name:
        return None
    return next((c for k, c in LOGO_CODES.items() if name.upper().startswith(k.upper())), None)


@lru_cache(maxsize=None)
def logo_b64(code: str) -> str:
    f = LOGO_DIR / f"{code}.png"
    return base64.b64encode(f.read_bytes()).decode() if f.exists() else ""


def logo_css(reports: list[dict]) -> str:
    """Each team's badge embedded once per page, reused by every card that needs it."""
    codes = sorted({c for r in reports for n in (r["game"].get("away"), r["game"].get("home"))
                    if (c := logo_code(n)) and logo_b64(c)})
    if not codes:
        return ""
    return "<style>" + "".join(f".tm-{c}{{background-image:url(data:image/png;base64,{logo_b64(c)})}}"
                               for c in codes) + "</style>"


TEAMS = {   # 로고가 없는 팀(상무 등)용 색 + 약칭
    "LG": ("LG", "#C30452"), "두산": ("두산", "#131230"), "KT": ("kt", "#111111"), "KIA": ("KIA", "#EA0029"),
    "삼성": ("삼성", "#074CA1"), "롯데": ("롯데", "#041E42"), "한화": ("한화", "#FC4E00"), "NC": ("NC", "#315288"),
    "키움": ("키움", "#570514"), "SSG": ("SSG", "#CE0E2D"), "상무": ("상무", "#33503B"),
}


def team_mark(name: str | None) -> str:
    if not name:
        return ""
    code = logo_code(name)
    if code and logo_b64(code):
        return f'<span class="tm tm-{code}" role="img" aria-label="{html.escape(name)}" title="{html.escape(name)}"></span>'
    key = next((k for k in TEAMS if name.upper().startswith(k.upper())), None)
    if not key:
        return f'<span class="tm" style="--tm-bg:var(--ink-2)">{html.escape(name[:2])}</span>'
    label, bg = TEAMS[key]
    return f'<span class="tm" style="--tm-bg:{bg}" title="{html.escape(name)}">{html.escape(label)}</span>'


def _vclass(icon: str) -> str:
    return {"🔴": "bad", "🟠": "warn", "🟡": "warn", "⚪": "none"}.get(icon, "ok")


def league_summary(rs: list[dict]) -> str:
    if not rs:
        return "경기가 없습니다."
    risky = [r for r in rs if r["rain"]["verdict_icon"] in ("🟠", "🔴")]
    maybe = [r for r in rs if r["rain"]["verdict_icon"] == "🟡"]
    hot = [r for r in rs if r["heat"].get("level") in ("warning", "danger")]
    parts = [f"{len(rs)}경기"]
    if risky:
        parts.append("우천 변수: " + ", ".join(f"{r['stadium']['short']} {pct(r['rain']['p_cancel'])}" for r in risky))
    if maybe:
        parts.append("비 가능성: " + ", ".join(r["stadium"]["short"] for r in maybe))
    if not risky and not maybe:
        parts.append("전 구장 강수 걱정 없음")
    if hot:
        parts.append("폭염 주의: " + ", ".join(f"{r['stadium']['short']} 체감 {r['heat']['max_apparent']}℃" for r in hot))
    carries = [(r, next(d for d in r["carry"]["directions"] if d["direction"] == "CF")["delta_vs_ref_m"])
               for r in rs if r["carry"] and not r["stadium"]["dome"] and r["stadium"]["cf_azimuth"] is not None]
    if carries:
        best = max(carries, key=lambda x: x[1])
        worst = min(carries, key=lambda x: x[1])
        parts.append(f"타구가 가장 뻗는 곳 {best[0]['stadium']['short']} {best[1]:+.1f} m, "
                     f"가장 안 뻗는 곳 {worst[0]['stadium']['short']} {worst[1]:+.1f} m".replace("-", "−"))
    return " · ".join(parts)


def _when(iso: str, ref: dt.datetime) -> str:
    t = dt.datetime.fromisoformat(iso)
    return f"{t:%H:%M}" if t.date() == ref.date() else f"{t.month}/{t.day} {t:%H:%M}"


def data_stamp(day: dict) -> str:
    """When the weather data behind this report was pulled, and which 기상청 issue it used."""
    t = dt.datetime.fromisoformat(day["generated_at"])
    bases = [r["sources"].get("kma") or {} for r in day["reports"]]
    issued = [f"{label} {_when(b, t)}" for key, label in (("short_base", "단기예보"), ("ultra_base", "초단기예보"))
              if (b := max((x[key] for x in bases if x.get(key)), default=None))]
    return (f"기상 데이터 {t.month}/{t.day} {t:%H:%M} 업데이트"
            + (f" · 기상청 {' · '.join(issued)} 발표분" if issued else " · 기상청 자료 없음(앙상블·모델만)"))


def game_card(r: dict, folds: bool = True) -> str:
    e = html.escape
    g, st, rain, cond, carry, heat = r["game"], r["stadium"], r["rain"], r["conditions"], r["carry"], r["heat"]
    q = rain.get("game_mm") or {}
    kma_tag = f' + 기상청(강수확률 최고 {rain["kma"]["pop_max"]} %)' if rain.get("kma") else ""
    dirs = {d["direction"]: d for d in carry["directions"]} if carry else {}
    status = status_suffix(g).strip(" []")
    starters = ""
    if g.get("away_starter") or g.get("home_starter"):
        starters = f"선발 {e(g.get('away_starter') or '-')} · {e(g.get('home_starter') or '-')}"

    match = (f'<div class="match"><div class="time">{e(r["start"][11:16])}</div>'
             f'<div class="venue">{e(st["short"])}<span class="tag">{e(r["league_name"])}</span>'
             + (f'<span class="tag kbo">{e(status)}</span>' if status else "") + "</div>"
             f'<div class="teams">{team_mark(g.get("away"))}<span>{e(r["label"])}</span>{team_mark(g.get("home"))}</div>'
             f'<div class="meta">{e(st["name"])}{(" · " + starters) if starters else ""}</div></div>')

    meters = "".join(
        f'<div class="meter"><span class="lbl">{k}</span><span class="val">{pct(v)}</span><i style="--w:{round((v or 0) * 100)}%"></i></div>'
        for k, v in (("경기중 강수 확률", rain["p_rain"]), ("중단 확률", rain["p_delay"]), ("취소 확률", rain["p_cancel"])))
    rainblock = (f'<div class="rain"><div class="verdict {_vclass(rain["verdict_icon"])}">{e(rain["verdict"])}</div>'
                 f'<div class="meters">{meters}</div>'
                 f'<div class="fine">경기창 {rain["window"][0]}–{rain["window"][1]} · 강수 중앙값 {q.get("p50", 0)} mm / 상위 10 % {q.get("p90", 0)} mm'
                 f' · 앙상블 {rain["members"]}멤버 + 모델 {len(rain["det"])}개{kma_tag}</div>'
                 + (f'<div class="fine hot">🌡 {e(heat["text"])} — 최고 체감 {heat["max_apparent"]}℃ ({e(heat["at"][11:16])})</div>'
                    if heat.get("level") not in (None, "ok", "unknown") else "") + "</div>")

    if carry:
        cf = dirs.get("CF", {})
        if st["dome"]:
            big, sub = signed(carry["air_only_delta_m"]), "돔 · 공기밀도만 반영"
        elif st["cf_azimuth"] is None:
            big, sub = signed(carry["air_only_delta_m"]), "방위각 미확정 · 바람 미반영"
        else:
            big = signed(cf.get("delta_vs_ref_m"))
            sub = f'좌 {signed(dirs["LF"]["delta_vs_ref_m"])} · 우 {signed(dirs["RF"]["delta_vs_ref_m"])} · 홈런 ×{carry["hr_multiplier"]:.2f}'
        up = "up" if (cf.get("delta_vs_ref_m") or 0) >= 2.0 and not st["dome"] else ""
        wind = "-"
        if cond.get("wind_speed") is not None:
            wind = f'<b>{e(cond["wind_compass"])}풍 {cond["wind_speed"]} m/s</b>'
            if not st["dome"] and st["cf_azimuth"] is not None:
                t = cf.get("tail_ms") or 0.0
                wind += f' → 외야 {"순풍" if t >= 0 else "역풍"} {abs(t):.1f} m/s'
        air = f'{cond.get("temp")}℃ · 습도 {cond.get("rh")} % · {cond.get("pressure_msl")} hPa'
        fieldblock = (f'<div class="field">{field_svg(st, cond, carry)}'
                      f'<div class="carry"><span class="big {up}">{big}</span><br><span class="sub">비거리 지수 · {sub}</span></div>'
                      f'<div class="wind">{wind}</div><div class="air">{e(cond["time"][11:16])} {air}</div></div>')
    else:
        fieldblock = f'<div class="field">{field_svg(st, cond, None)}<div class="carry"><span class="sub">기상 조건 부족</span></div></div>'

    hours = ""
    if r["hourly"]:
        rows = []
        for h in r["hourly"]:
            wet = (h.get("p_ens_wet") or 0) >= 0.3
            rows.append(f'<tr><td>{e(h["time"][11:16])}</td><td>{h["temp"]}</td><td>{h.get("apparent", "-")}</td><td>{h["rh"]}</td>'
                        f'<td class="{"wet" if wet else ""}">{h["precip_p50"]} / {h["precip_max"]}</td><td class="{"wet" if wet else ""}">{pct(h.get("p_ens_wet"))}</td>'
                        f'<td>{"-" if h.get("pop_models") is None else str(round(h["pop_models"])) + " %"}</td>'
                        f'<td>{"-" if h.get("pop_kma") is None else str(round(h["pop_kma"])) + " %"}</td>'
                        f'<td>{e(h["wind_compass"])} {h["wind_speed"]}</td><td>{h["cloud"]}</td></tr>')
        pdata = [{"t": h["time"][11:16], "temp": round(h["temp"]) if h.get("temp") is not None else "-",
                  "rh": h.get("rh"), "spd": round(h.get("wind_speed") or 0.0, 1), "dir": h.get("wind_dir") or 0,
                  "comp": h.get("wind_compass") or "", "p": round(h.get("p_ens_wet") if h.get("p_ens_wet") is not None
                                                                  else (h.get("pop_kma") or 0) / 100, 3)}
                 for h in r["hourly"]]
        play = (f'<div class="play" data-hours="{e(json.dumps(pdata, ensure_ascii=False))}">'
                f'<div class="stage"><div class="plane">{field_svg(st, None, None, streaks=True)}</div>'
                '<div class="rain" aria-hidden="true"></div></div>'
                '<div class="controls"><button type="button" class="pbtn">▶ 재생</button>'
                f'<input type="range" class="scrub" min="0" max="{len(pdata) - 1}" step="1" value="0" aria-label="시간 선택">'
                '<div class="rvals"></div>'
                '<div class="pnote">예보는 1시간 단위 · 사이 시간은 앞뒤 값을 이어서 그립니다</div></div></div>')
        hours = (f'<details><summary>시간대별 · {len(r["hourly"])}시간</summary>{play}<div class="wrap"><table>'
                 '<tr><th>시각</th><th>기온 ℃</th><th>체감 ℃</th><th>습도 %</th><th>강수 중앙/최대 mm</th><th>비 확률 (앙상블)</th><th>모델 POP</th><th>기상청 POP</th><th>바람 m/s</th><th>구름 %</th></tr>'
                 + "".join(rows) + "</table></div></details>")

    carrybox = f'<div class="carrybox">{fieldblock}{flight_svg(r)}</div>'
    return (f'<article class="game">{match}{rainblock}'
            + (explain(r) + hours if folds else "") + carrybox + "</article>")


LEAGUES = ((1, "1군", "kbo"), (2, "퓨처스", "futures"))

TAB_JS = """<script>
(function () {
  var tabs = [].slice.call(document.querySelectorAll('[role="tab"]'));
  function show(t, focus) {
    tabs.forEach(function (x) {
      var on = x === t;
      x.setAttribute("aria-selected", on ? "true" : "false");
      x.tabIndex = on ? 0 : -1;
      document.getElementById(x.getAttribute("aria-controls")).hidden = !on;
    });
    if (focus) t.focus();
  }
  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () {
      show(t);
      try { history.replaceState(null, "", "#" + t.dataset.key); } catch (e) {}
    });
    t.addEventListener("keydown", function (ev) {
      var d = ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : 0;
      if (d) { ev.preventDefault(); show(tabs[(i + d + tabs.length) % tabs.length], true); }
    });
  });
  var h = (location.hash || "").slice(1);
  tabs.forEach(function (t) { if (t.dataset.key === h) show(t); });
})();
</script>"""


PLAY_JS = """<script>
(function () {
  var STEPS = 12;                       // 한 시간을 12칸(5분)으로 나눠 부드럽게 이어 그림
  document.querySelectorAll(".play").forEach(function (box) {
    var hours = [];
    try { hours = JSON.parse(box.dataset.hours || "[]"); } catch (e) { return; }
    if (!hours.length) return;
    var rose = box.querySelector(".windrose"), rain = box.querySelector(".rain"),
        btn = box.querySelector(".pbtn"), scrub = box.querySelector(".scrub"),
        out = box.querySelector(".rvals"), last = hours.length - 1,
        frames = last * STEPS, timer = null, f = 0, angle = null, shown = null;
    scrub.max = Math.max(frames, 1);
    function lerp(a, b, u) { return a + (b - a) * u; }
    function at(frame) {                 // 앞뒤 시간값을 섞어 그 사이 값을 만든다
      var i = Math.min(last, Math.floor(frame / STEPS)), u = (frame - i * STEPS) / STEPS,
          a = hours[i], b = hours[Math.min(last, i + 1)];
      var ax = a.spd * Math.cos(a.dir * Math.PI / 180), ay = a.spd * Math.sin(a.dir * Math.PI / 180),
          bx = b.spd * Math.cos(b.dir * Math.PI / 180), by = b.spd * Math.sin(b.dir * Math.PI / 180),
          x = lerp(ax, bx, u), y = lerp(ay, by, u);
      var mins = Math.round((+a.t.slice(0, 2) * 60 + +a.t.slice(3)) + u * 60);
      return {t: ("0" + Math.floor(mins / 60) % 24).slice(-2) + ":" + ("0" + mins % 60).slice(-2),
              temp: lerp(a.temp, b.temp, u), rh: lerp(a.rh == null ? 0 : a.rh, b.rh == null ? 0 : b.rh, u),
              p: lerp(a.p, b.p, u), spd: Math.hypot(x, y),
              dir: (Math.atan2(y, x) * 180 / Math.PI + 360) % 360, comp: u < .5 ? a.comp : b.comp};
    }
    function show(frame) {
      f = Math.max(0, Math.min(frames, frame));
      var h = at(f);
      scrub.value = f;
      if (rose) {
        var target = (h.dir + 180) % 360 - 90;
        if (angle === null) { angle = target; }
        else { angle += ((target - angle + 540) % 360) - 180; }   // 짧은 쪽으로 돌린다
        rose.style.setProperty("--to", angle.toFixed(1) + "deg");
        var dur = Math.max(1.1, Math.min(6, 14 / Math.max(h.spd, 0.3))).toFixed(1) + "s";
        if (dur !== shown) {
          shown = dur;
          box.querySelectorAll(".streak").forEach(function (l) { l.style.setProperty("--dur", dur); });
        }
      }
      if (rain) rain.style.opacity = h.p > 0.02 ? Math.min(0.8, 0.12 + h.p) : 0;
      out.innerHTML = "<b>" + h.t + "</b> · " + h.temp.toFixed(1) + "\u2103 · 습도 " + Math.round(h.rh)
        + "% · 비 확률 " + Math.round(h.p * 100) + "% · " + h.comp + "풍 " + h.spd.toFixed(1) + " m/s";
    }
    function stop() { clearInterval(timer); timer = null; btn.textContent = "▶ 재생"; }
    btn.addEventListener("click", function () {
      if (timer) { stop(); return; }
      if (f >= frames) show(0);
      btn.textContent = "❚❚ 멈춤";
      timer = setInterval(function () { if (f >= frames) { stop(); } else { show(f + 1); } }, 110);
    });
    scrub.addEventListener("input", function () { stop(); show(+scrub.value); });
    show(0);
  });
})();
</script>"""


def to_html(day: dict) -> str:
    e = html.escape
    kma_used = any(r["rain"].get("kma") for r in day["reports"])
    parts = [f'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             f"<title>KBO 직관 날씨 예보</title>{FONT_LINK}<style>{CSS}</style>{logo_css(day['reports'])}<main>",
             '<header class="top"><div class="eyebrow">KBO 직관 날씨 예보</div>',
             f"<h1>{e(date_title(day['date']))} 경기 날씨</h1>",
             f'<p class="stamp">{e(data_stamp(day))}</p>',
             "</header>"]
    by = {le: [r for r in day["reports"] if r["game"]["league"] == le] for le, _, _ in LEAGUES}
    skip = {le: [s for s in day.get("skipped", []) if s["game"].get("league") == le] for le, _, _ in LEAGUES}
    default = 1 if by[1] or skip[1] or not (by[2] or skip[2]) else 2   # 1군 first; 퓨처스 only when 1군 is idle
    parts.append('<div class="tabs" role="tablist" aria-label="리그 선택">')
    for le, name, key in LEAGUES:
        on = le == default
        parts.append(f'<button type="button" class="tab" role="tab" id="tab-{key}" data-key="{key}" '
                     f'aria-controls="panel-{key}" aria-selected="{str(on).lower()}" tabindex="{0 if on else -1}">'
                     f'{name}<small>{len(by[le]) + len(skip[le])}경기</small></button>')
    parts.append("</div>")
    for le, name, key in LEAGUES:
        hidden = "" if le == default else " hidden"
        parts.append(f'<section class="panel" role="tabpanel" id="panel-{key}" aria-labelledby="tab-{key}"{hidden}>')
        if by[le]:
            parts.append(f'<p class="summary">{e(league_summary(by[le]))}</p>')
            parts.extend(game_card(r) for r in by[le])
        if skip[le]:
            parts.append('<ul class="skip">' + "".join(
                f"<li>{e(s['game'].get('time', ''))} {e(str(s['game'].get('stadium_raw')))} · "
                f"{e(s['game'].get('away', ''))} vs {e(s['game'].get('home', ''))} — 계산하지 못했습니다: {e(s['reason'])}</li>"
                for s in skip[le]) + "</ul>")
        if not by[le] and not skip[le]:
            parts.append(f'<p class="empty">이 날은 {name} 경기가 없습니다.</p>')
        parts.append("</section>")
    parts.append(
        '<section class="legend">'
        '<b>강수 확률</b>은 ECMWF·AIFS·GEFS·ICON·GEM·UKMO 앙상블 멤버 각각을 하나의 날씨 시나리오로 보고, 경기 시간창(시작 1시간 전~종료)의 누적·최대 강수로 '
        '중단/취소 조건을 판정한 뒤 시스템별 비율을 평균한 값(75 %)에 고해상도 모델 7개의 동의율(25 %)을 섞은 것입니다. '
        + ('여기에 기상청 단기·초단기예보의 강수확률을 경기까지 남은 시간에 따라 25 %(하루 이상 전)에서 50 %(6시간 이내)까지 비중을 두어 함께 반영했습니다. '
           if kma_used else '이번 계산에서는 기상청 자료를 받지 못해 앙상블과 고해상도 모델만 썼습니다. ')
        +         '<b>비거리 지수</b>는 100 mph·28°·1800 rpm 표준 타구를 오늘 공기밀도와 구장 외야 방향의 바람으로 시뮬레이션해 표준 대기(20 ℃·1013 hPa·50 %·무풍, 124.8 m)와 비교한 값이며, '
        '관중석이 바람을 막는 정도는 실측 자료가 없어 구장 구조를 보고 정한 추정값(1군 구장 50–55 %, 개방형 퓨처스 구장 80–85 %)을 10 m 풍속에 곱했습니다. 그림은 북쪽이 위인 구장 평면도이고 주황 화살표가 바람이 불어가는 방향입니다. '
        '<b>폭염</b>은 기상청 여름철 체감온도 산출식과 KBO 2026-08 개정 기준(체감 35 ℃ 취소 가능, 33 ℃ 지연 가능)을 따릅니다. '
        f'생성 {e(day["generated_at"][:16])} · 데이터 {"기상청(단기예보 조회서비스), " if kma_used else ""}Open-Meteo, KBO 공식 일정 · 위성 판독 구장 방위각(±10°)'
        '</section></main>' + TAB_JS + PLAY_JS)
    return "".join(parts)
