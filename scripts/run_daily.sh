#!/bin/zsh
# 매일 자동 실행용. launchd(plist) 또는 cron 에서 호출.
#   08:30  1군+퓨처스 전체 (퓨처스 11:00/13:00 경기 대비)
#   15:30  1군만 갱신 (18:30 경기 3시간 전, 최신 모델 반영)
set -euo pipefail
cd "$(dirname "$0")/.."
LEAGUE="${1:-all}"
shift || true
/usr/bin/env python3 -m kboweather today --league "$LEAGUE" "$@" >> out/run.log 2>&1
