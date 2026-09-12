#!/bin/bash
# 공유 대시보드를 기본 브라우저로 연다. 인터넷이 안 되면 맥에 있는 최신 리포트를 연다.
URL="https://woojeongryeol.github.io/kbo-weather-forecast/"
LOCAL="$HOME/KBO Weather Forecast/out/latest.html"
if /usr/bin/curl -sS -m 4 -o /dev/null "$URL"; then
  exec /usr/bin/open "$URL"
elif [ -f "$LOCAL" ]; then
  exec /usr/bin/open "$LOCAL"
else
  exec /usr/bin/open "$URL"
fi
