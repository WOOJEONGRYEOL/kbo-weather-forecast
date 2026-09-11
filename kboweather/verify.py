"""Head-to-head scoring: 기상청 vs 앙상블 vs 글로벌 모델 vs the final blend.

Reads data/history/forecasts.jsonl (written by every run) and, for games whose
date has passed, asks KBO what happened: y = 1 if the game was called off for
우천취소 / 그라운드사정 (heat cancellations are dropped). Brier score per source
and lead time — lower is better. This is what answers "기상청보다 정확한가?".
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from .kbo import fetch_games

RAIN = {"우천취소", "그라운드사정"}
NOT_RAIN = {"폭염취소", "미세먼지취소", "기타"}
BINS = (("경기 6시간 이내", 0, 6), ("6~24시간 전", 6, 24), ("하루 이상 전", 24, float("inf")))
SOURCES = (("final", "최종"), ("kma", "기상청"), ("ens", "앙상블"), ("det", "글로벌 모델"))


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def score(path: Path, cache_dir: Path, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    rows = [r for r in load(path) if dt.date.fromisoformat(r["date"]) < today]
    if not rows:
        return "아직 채점할 기록이 없습니다. 매일 실행하면 경기가 끝난 날부터 채점됩니다."
    outcome = {}
    for date, league in sorted({(r["date"], r["league"]) for r in rows}):
        for g in fetch_games(dt.date.fromisoformat(date), league, cache_dir):
            outcome[g.game_id] = g.cancel
    latest = {}                                    # last forecast per (game, lead bin)
    for r in rows:
        b = next(name for name, lo, hi in BINS if lo <= r["lead_h"] < hi)
        latest[(r["game_id"], b)] = r
    pairs = defaultdict(lambda: defaultdict(list))
    counts = defaultdict(lambda: [0, 0])
    for (gid, b), r in latest.items():
        c = outcome.get(gid)
        if c is None or c in NOT_RAIN:
            continue
        y = 1 if c in RAIN else 0
        counts[b][0] += 1
        counts[b][1] += y
        for key, _ in SOURCES:
            if r.get(key) and r[key].get("cancel") is not None:
                pairs[b][key].append((r[key]["cancel"], y))
    lines = ["취소 확률 Brier 점수 — 낮을수록 정확 (0 = 완벽, 항상 0 %로 찍으면 = 실제 취소율)"]
    for name, _, _ in BINS:
        n, k = counts[name]
        if not n:
            continue
        cells = [f"{label} {sum((p - y) ** 2 for p, y in ps) / len(ps):.3f}"
                 for key, label in SOURCES if (ps := pairs[name][key])]
        lines.append(f"  {name}: {n}경기 (우천·그라운드 취소 {k}) · " + " · ".join(cells))
    return "\n".join(lines)
