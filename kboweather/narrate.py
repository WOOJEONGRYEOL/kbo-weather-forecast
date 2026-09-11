"""Korean briefing text: deterministic template + optional local LLM (Ollama)."""
from __future__ import annotations

import json
import re
import urllib.request

from .report import to_markdown


def template(day: dict) -> str:
    return to_markdown(day)


def _compact(day: dict) -> list[dict]:
    rows = []
    for r in day["reports"]:
        c, rain, carry, heat = r["conditions"], r["rain"], r["carry"] or {}, r["heat"]
        dirs = {d["direction"]: d for d in carry.get("directions", [])}
        rows.append({
            "리그": r["league_name"], "시작": r["start"][11:16], "구장": r["stadium"]["name"], "경기": r["label"],
            "판정": rain["verdict"], "경기중비확률": rain["p_rain"], "중단확률": rain["p_delay"], "취소확률": rain["p_cancel"],
            "경기창강수mm_중앙값": (rain.get("game_mm") or {}).get("p50"),
            "기온": c.get("temp"), "습도": c.get("rh"), "바람": f"{c.get('wind_compass')}풍 {c.get('wind_speed')}m/s",
            "비거리변화m_중앙": dirs.get("CF", {}).get("delta_vs_ref_m"), "외야방향바람m/s": dirs.get("CF", {}).get("tail_ms"),
            "홈런기대배수": carry.get("hr_multiplier"), "폭염": heat.get("text") if heat.get("level") not in ("ok", None) else None,
        })
    return rows


PROMPT = """당신은 KBO 야구 전문 기상 캐스터입니다. 아래는 오늘 경기별 구장 핀포인트 기상 분석 데이터(JSON)입니다.
이 데이터만 근거로, 라디오 캐스터처럼 자연스러운 한국어 브리핑을 작성하세요.
규칙: 1) 숫자를 지어내지 말 것 2) 취소/중단 위험이 큰 경기부터 언급 3) 비거리·바람이 특이한 구장은 재미있게 짚어줄 것
4) 폭염 항목이 있으면 관중 안전 멘트 5) 전체 400~600자, 마크다운 없이 문장으로.

데이터:
"""


def ollama(day: dict, host: str = "http://localhost:11434", model: str = "gemma4:12b",
           timeout: int = 240) -> str | None:
    body = {"model": model, "stream": False, "think": False,
            "prompt": PROMPT + json.dumps(_compact(day), ensure_ascii=False),
            "options": {"temperature": 0.6, "num_predict": 900}}
    req = urllib.request.Request(host.rstrip("/") + "/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = json.load(r).get("response", "")
    except Exception:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return text or None
