"""Render a day report as Markdown (Telegram/console) and a self-contained HTML dashboard."""
from __future__ import annotations

import datetime as dt
import html
import math

from .explain import explain

WEEKDAYS = "월화수목금토일"


def pct(x) -> str:
    return "-" if x is None else f"{round(x * 100):d}%"


def signed(x, unit="m") -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    x = 0.0 if abs(x) < 0.05 else x           # no "-0.0"
    return f"{x:+.1f}{unit}"


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
    lines.append(f"  강수: 경기중 비 {pct(rain['p_rain'])} / 중단 {pct(rain['p_delay'])} / 취소 {pct(rain['p_cancel'])}"
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
    out = [f"⚾ KBO 구장별 기상 브리핑 — {date_title(day['date'])}", ""]
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
    out = [f"⚾ <b>{d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]}) KBO 구장 날씨</b>", f"<i>{e(data_stamp(day))}</i>"]
    for le, name, _ in LEAGUES:
        rs = [r for r in day["reports"] if r["game"]["league"] == le]
        if not rs:
            continue
        out.append(f"\n<b>{name}</b> · {e(league_summary(rs))}")
        for r in rs:
            st, rain, cond, carry = r["stadium"], r["rain"], r["conditions"], r["carry"]
            box = [f"<b>{e(r['start'][11:16])} {e(st['short'])}</b> · {e(r['label'])}{e(status_suffix(r['game']))}",
                   f"{rain['verdict_icon']} {e(rain['verdict'])}",
                   f"☔ 비 {pct(rain['p_rain'])} · 취소 {pct(rain['p_cancel'])}"]
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
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--body)}
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
.game>details{grid-column:1/-1}
@media(max-width:760px){.game{grid-template-columns:1fr}}
.time{font:700 36px/1 var(--display);font-variant-numeric:tabular-nums}
.venue{font:600 19px/1.2 var(--display);margin-top:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tag{font:500 11px/1 var(--body);letter-spacing:.08em;border:1px solid var(--line);border-radius:3px;padding:3px 6px;color:var(--ink-2);white-space:nowrap}
.tag.kbo{color:var(--ink);border-color:var(--ink-2)}
.teams{margin-top:6px;font-weight:500}
.meta{color:var(--ink-2);font-size:12.5px;margin-top:2px}
.verdict{font-weight:600;display:flex;align-items:center;gap:8px}
.verdict::before{content:"";flex:none;width:10px;height:10px;border-radius:50%;background:var(--ok)}
.verdict.warn::before{background:var(--warn)}.verdict.bad::before{background:var(--bad)}.verdict.none::before{background:var(--line)}
.meters{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:10px}
.meter{display:grid;grid-template-columns:1fr auto;align-items:baseline;row-gap:5px}
.meter .lbl{font-size:12px;color:var(--ink-2)}
.meter .val{font:600 22px/1 var(--display);font-variant-numeric:tabular-nums}
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
"""


def field_svg(st: dict, cond: dict, carry: dict | None) -> str:
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
    parts.append("</svg>")
    return "".join(parts)


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
             f'<div class="teams">{e(r["label"])}</div>'
             f'<div class="meta">{e(st["name"])}{(" · " + starters) if starters else ""}</div></div>')

    meters = "".join(
        f'<div class="meter"><span class="lbl">{k}</span><span class="val">{pct(v)}</span><i style="--w:{round((v or 0) * 100)}%"></i></div>'
        for k, v in (("경기 중 비", rain["p_rain"]), ("중단 가능", rain["p_delay"]), ("취소 가능", rain["p_cancel"])))
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
        hours = (f'<details><summary>시간대별 · {len(r["hourly"])}시간</summary><div class="wrap"><table>'
                 '<tr><th>시각</th><th>기온 ℃</th><th>체감 ℃</th><th>습도 %</th><th>강수 중앙/최대 mm</th><th>비 확률 (앙상블)</th><th>모델 POP</th><th>기상청 POP</th><th>바람 m/s</th><th>구름 %</th></tr>'
                 + "".join(rows) + "</table></div></details>")

    return f'<article class="game">{match}{rainblock}{fieldblock}{explain(r) + hours if folds else ""}</article>'


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


def to_html(day: dict) -> str:
    e = html.escape
    kma_used = any(r["rain"].get("kma") for r in day["reports"])
    parts = [f'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             f"<title>KBO 구장 기상 브리핑</title>{FONT_LINK}<style>{CSS}</style><main>",
             '<header class="top"><div class="eyebrow">KBO · 구장 핀포인트 기상</div>',
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
        '기상청 단기·초단기예보가 연결돼 있으면 기상청 강수확률도 경기까지 남은 시간에 따라 25 %(하루 이상 전)~50 %(6시간 이내) 비중으로 함께 반영합니다. '
        '<b>비거리 지수</b>는 100 mph·28°·1800 rpm 표준 타구를 오늘 공기밀도와 구장 외야 방향의 바람으로 시뮬레이션해 표준 대기(20 ℃·1013 hPa·50 %·무풍, 124.8 m)와 비교한 값이며, '
        '관중석이 바람을 막는 정도는 실측 자료가 없어 구장 구조를 보고 정한 추정값(1군 구장 50–55 %, 개방형 퓨처스 구장 80–85 %)을 10 m 풍속에 곱했습니다. 그림은 북쪽이 위인 구장 평면도이고 주황 화살표가 바람이 불어가는 방향입니다. '
        '<b>폭염</b>은 기상청 여름철 체감온도 산출식과 KBO 2026-08 개정 기준(체감 35 ℃ 취소 가능, 33 ℃ 지연 가능)을 따릅니다. '
        f'생성 {e(day["generated_at"][:16])} · 데이터 {"기상청(단기예보 조회서비스), " if kma_used else ""}Open-Meteo, KBO 공식 일정 · 위성 판독 구장 방위각(±10°)'
        '</section></main>' + TAB_JS)
    return "".join(parts)
