"""Plain-language reasons for each game card — deterministic templates, no LLM.

Every number is read from the report dict; nothing is invented. The order is
what a fan asks: 비 오나? → 바람은? → 공이 잘 뻗나? → 덥나? → (기준·구장 메모)
"""
from __future__ import annotations

import html
import re

from .forecast import compass

DET_NAMES = {
    "jma_msm": "일본 MSM", "ecmwf_ifs": "유럽 ECMWF", "icon_seamless": "독일 ICON",
    "ukmo_seamless": "영국 UKMO", "gfs_seamless": "미국 GFS", "gem_global": "캐나다 GEM",
    "meteofrance_arpege_world": "프랑스 ARPEGE", "kma_ldps": "기상청 LDAPS",
    "google_weathernext3": "구글 WeatherNext 3",
}


def _pct(x: float) -> str:
    return f"{round(x * 100)} %"


def _m(x: float) -> str:
    x = 0.0 if abs(x) < 0.05 else x
    return f"{x:+.1f} m".replace("-", "−")


def _count(rain: dict, event: str) -> int:
    return sum(round(v[event] * v["n"]) for v in rain["systems"].values())


def rain_text(r: dict) -> str:
    rain, st = r["rain"], r["stadium"]
    if st["dome"]:
        return ("이 구장은 지붕이 있는 돔이라 비가 와도 경기는 그대로 진행됩니다. "
                f"구장을 오가는 길에 비를 만날 확률은 {_pct(rain['p_rain'])}입니다.")
    n, det, th = rain["members"], rain["det"], rain["thresholds"]
    if not n and not det:
        return "예보 기간(3일)을 벗어난 날짜라 계산하지 못했습니다."
    w0, w1 = rain["window"]
    out = []
    if n:
        wet, stop, cancel = (_count(rain, k) for k in ("rain", "delay", "cancel"))
        out.append(f"경기 시작 1시간 전부터 끝날 때까지({w0}~{w1}), 세계 {len(rain['systems'])}개 예보 기관이 "
                   f"내놓은 날씨 시나리오 {n}개를 하나씩 따져봤습니다.")
        if wet == 0:
            out.append("비가 내리는 시나리오는 하나도 없었습니다.")
        else:
            out.append(f"그중 {wet}개({_pct(wet / n)})에서 경기 중 비가 내립니다.")
            more = []
            if stop:
                more.append(f"시간당 {th['delay_rate']:g} mm를 넘겨 경기를 멈출 만한 비가 {stop}개")
            if cancel:
                more.append(f"취소될 만큼 많은 비가 {cancel}개")
            if more:
                out.append(", ".join(more) + "입니다.")
    if not n and det:
        out.append(f"이번에는 앙상블 자료를 받지 못해 고해상도 예보 모델 {len(det)}개로만 판단했습니다.")
    if det:
        wet_models = [DET_NAMES.get(m, m) for m, v in det.items() if v["rain"]]
        top_m, top_v = max(det.items(), key=lambda kv: kv[1]["game"])
        if wet_models:
            out.append(f"고해상도 예보 모델 {len(det)}개 중에서는 {len(wet_models)}개({', '.join(wet_models)})가 "
                       f"비를 예상했고, 가장 많이 본 모델({DET_NAMES.get(top_m, top_m)})은 경기 중 "
                       f"{top_v['game']:.1f} mm를 내다봤습니다.")
        elif top_v["game"] < 0.1:
            out.append(f"고해상도 예보 모델 {len(det)}개도 모두 강수 0 mm로 봤습니다.")
        else:
            out.append(f"고해상도 예보 모델 {len(det)}개도 모두 {th['rain_total']:g} mm 미만, 빗방울 수준으로 봤습니다.")
    k = rain.get("kma")
    if k:
        out.append(f"기상청 {k['source']}는 경기 시간 강수확률을 최고 {k['pop_max']} %로 봤고, "
                   f"예상 강수량은 {k['game_mm']:g} mm입니다.")
        w, lead = (rain.get("weights") or {}).get("kma", 0), rain.get("lead_h")
        if w and lead is not None and lead >= 0:
            out.append(f"경기까지 약 {max(1, round(lead))}시간 남아 기상청 예보를 {round(w * 100)} % 비중으로 반영했습니다.")
            out.append(f"그래서 최종 '경기중 강수 확률'은 {_pct(rain['p_rain'])}입니다.")
    pre = rain.get("pre_mm_p50") or 0.0
    if pre >= th["wet_pre"] * 0.5:
        out.append(f"경기 전 6시간 동안에도 비가 {pre:g} mm(중앙값) 내려 그라운드 상태가 변수입니다.")
    return " ".join(out)


def criteria_text(r: dict) -> str:
    rain, st, g = r["rain"], r["stadium"], r["game"]
    if st["dome"] or (not rain["members"] and not rain["det"]):
        return ""
    th = rain["thresholds"]
    s = (f"판정 기준 — 시간당 {th['delay_rate']:g} mm가 넘으면 중단, 5회를 마치기 전(시작 후 2시간) 시간당 "
         f"{th['cancel_rate']:g} mm 이상이거나 경기 중 누적 {th['cancel_total']:g} mm 이상이면 취소로 봅니다. "
         "2026 시즌 실제 취소 기록(우천·그라운드사정 140건)으로 검증한 값입니다.")
    if g["league"] == 2:
        s += " 퓨처스는 1군보다 쉽게 취소되는 편이라 기준을 더 낮게 잡았습니다."
    if st.get("drainage") == "turf":
        s += " 인조잔디라 물이 잘 빠지므로 경기 전에 온 비에는 덜 민감하게 봤습니다."
    elif st.get("drainage") == "fair":
        s += " 1군 구장보다 배수가 약한 훈련장이라 경기 전에 온 비에는 더 민감하게 봤습니다."
    return s


def wind_text(r: dict) -> str:
    st, c, carry = r["stadium"], r["conditions"], r["carry"]
    if st["dome"]:
        return "돔 안이라 바람의 영향은 없습니다."
    if not c or c.get("wind_speed") is None:
        return ""
    out = []
    az = st["cf_azimuth"]
    if az is not None:
        out.append(f"{st['short']} 구장은 홈플레이트에서 봤을 때 중앙 담장이 {compass(az)}쪽({az:.0f}°)을 향합니다.")
    spd, t = c["wind_speed"], c["time"][11:16]
    if spd < 0.5:
        out.append(f"{t} 무렵 바람은 거의 없습니다(초속 {spd:.1f} m).")
        return " ".join(out)
    out.append(f"{t} 무렵에는 {compass(c['wind_dir'])}쪽에서 초속 {spd:.1f} m의 바람이 불 것으로 보입니다.")
    share = round(st["shelter"] * 100)
    if st["shelter"] >= 0.8:
        out.append(f"관중석이 낮은 훈련장이라 이 바람의 {share} % 정도가 그라운드까지 들어온다고 봤습니다(추정값).")
    else:
        out.append(f"높은 관중석이 바람을 막아 그라운드에는 {share} % 정도만 들어온다고 봤습니다(추정값).")
    if az is None or not carry:
        return " ".join(out)
    tail = next(x for x in carry["directions"] if x["direction"] == "CF")["tail_ms"]
    if abs(tail) < 0.3:
        out.append("타구 방향으로는 거의 옆바람이라 비거리에 주는 영향은 작습니다.")
    elif tail > 0:
        out.append(f"타구 방향으로 보면 중앙 쪽 뒷바람(초속 {tail:.1f} m)이 되어 공을 밀어줍니다.")
    else:
        out.append(f"타구 방향으로 보면 중앙 쪽 맞바람(초속 {-tail:.1f} m)이 되어 공을 붙잡습니다.")
    return " ".join(out)


def carry_text(r: dict) -> str:
    st, c, carry = r["stadium"], r["conditions"], r["carry"]
    if not carry or not c:
        return ""
    d = carry["density_delta_pct"]
    out = [f"{c['time'][11:16]} 예상 기온 {c['temp']}℃, 습도 {c['rh']} %, 기압 {c['pressure_msl']} hPa."]
    if abs(d) < 0.3:
        out.append("공기 무게는 표준(20℃·1013 hPa)과 거의 같습니다.")
    else:
        out.append(f"공기가 표준(20℃·1013 hPa)보다 {abs(d):.1f} % {'가벼워' if d < 0 else '무거워'}, "
                   f"공기만 따지면 비거리가 {_m(carry['air_only_delta_m'])} 달라집니다.")
    if (c.get("rh") or 0) >= 80:
        out.append("습한 공기는 수증기가 가벼워 오히려 공이 조금 더 뻗습니다.")
    if st["dome"]:
        out.append("다만 돔 안은 냉난방을 하므로 바깥 공기로 계산한 이 값은 참고용입니다.")
        return " ".join(out)
    if st["cf_azimuth"] is None:
        return " ".join(out)
    dirs = {x["direction"]: x for x in carry["directions"]}
    cf = dirs["CF"]
    out.append(f"바람까지 넣으면, 표준 조건에서 {carry['ref_distance_m']:.1f} m 날아가는 타구(시속 161 km·발사각 30°)가 "
               f"오늘은 중앙 쪽으로 {cf['distance_m']:.1f} m({_m(cf['delta_vs_ref_m'])}) 날아갑니다.")
    out.append(f"우타자가 당겨친 좌측 타구는 {_m(dirs['LF']['delta_vs_ref_m'])}, "
               f"좌타자가 당겨친 우측 타구는 {_m(dirs['RF']['delta_vs_ref_m'])}입니다.")
    hr = carry.get("hr_multiplier") or 1.0
    if abs(hr - 1) < 0.03:
        out.append("홈런 수에는 거의 영향이 없는 조건입니다.")
    else:
        out.append(f"비거리가 1 % 늘면 홈런은 6 %쯤 늘어나기 때문에, 평소보다 홈런이 "
                   f"{abs(hr - 1) * 100:.0f} % {'많이' if hr > 1 else '적게'} 나올 만한 날씨입니다.")
    return " ".join(out)


def heat_text(r: dict) -> str:
    h, st = r["heat"], r["stadium"]
    if "max_apparent" not in h:
        return ""
    if st["dome"]:
        return "돔 안은 냉방이 되어 더위 변수는 없습니다."
    at = h["at"][11:16]
    if h["level"] == "ok":
        return f"경기 시간 최고 체감온도는 {h['max_apparent']}℃({at})로 더위 걱정은 없습니다."
    return f"체감온도가 {at}에 {h['max_apparent']}℃까지 오릅니다. {h['text']}."


def venue_text(r: dict) -> str:
    st = r["stadium"]
    f = st.get("fences") or {}
    out = []
    if not st["dome"] and st["cf_azimuth"] is not None:
        m = re.search(r"±(\d+)", st.get("azimuth_source") or "")
        out.append("구장 방향은 위성사진에서 직접 잰 값" + (f"으로, 오차는 ±{m.group(1)}° 정도입니다." if m else "입니다."))
    if f.get("cf"):
        out.append(f"담장 거리는 좌 {f['lf']:g} m, 중앙 {f['cf']:g} m, 우 {f['rf']:g} m입니다.")
    if st.get("notes") and not st["dome"]:
        out.append(st["notes"])
    return " ".join(out)


def explain(r: dict) -> str:
    e = html.escape
    main = [("비", rain_text(r)), ("바람", wind_text(r)), ("비거리", carry_text(r)), ("더위", heat_text(r))]
    notes = [criteria_text(r), venue_text(r)]
    body = "".join(f"<p><b>{k}</b>{e(v)}</p>" for k, v in main if v)
    body += "".join(f'<p class="note">{e(v)}</p>' for v in notes if v)
    return f'<details class="why"><summary>예보 근거 읽기</summary><div class="why-body">{body}</div></details>'
