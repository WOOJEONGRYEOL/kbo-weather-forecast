#!/bin/bash
# launchd(08:30·15:30) → git pull → 이 시간대를 GitHub 가 이미 돌렸으면 건너뜀 → 실행 → 결과 커밋·푸시.
# 원격 저장소가 없거나 오프라인이어도 로컬 실행은 그대로 합니다.
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p out data/runs data/history
exec >> out/auto.log 2>&1
echo "=== $(date '+%F %T') local ==="

remote=$(git remote 2>/dev/null | head -1)
if [ -n "$remote" ]; then
  git pull --rebase --autostash -q || echo "git pull 실패 — 오프라인일 수 있어 로컬로만 실행"
fi

python3 -m kboweather auto --runner local || exit 1

files=$(git ls-files -mo --exclude-standard -- data/runs data/history out 2>/dev/null)
[ -z "$files" ] && exit 0
git add -- $files
git commit -q -m "local run $(date '+%F %H:%M')" || exit 0
if [ -n "$remote" ]; then
  for i in 1 2 3; do git push -q && break; git pull --rebase -q; sleep 3; done
fi
