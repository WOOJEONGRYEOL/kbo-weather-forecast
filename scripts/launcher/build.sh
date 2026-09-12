#!/bin/bash
# 바탕화면 런처 다시 만들기: scripts/launcher/icon.svg → .icns → ~/Desktop/KBO 경기장 날씨.app
set -e
cd "$(dirname "$0")/../.."
APP="$HOME/Desktop/KBO 경기장 날씨.app"
TMP=$(mktemp -d); ICONSET="$TMP/kbo.iconset"; mkdir -p "$ICONSET"
printf '<!doctype html><meta charset="utf-8"><style>html,body{margin:0}svg{display:block}</style>' > "$TMP/i.html"
cat scripts/launcher/icon.svg >> "$TMP/i.html"
data/cache/webshot "$TMP/i.html" "$TMP/icon.png" 1024 1
for sz in 16 32 128 256 512; do
  sips -Z $sz "$TMP/icon.png" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null
  sips -Z $((sz*2)) "$TMP/icon.png" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o scripts/launcher/icon.icns
rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp scripts/launcher/icon.icns "$APP/Contents/Resources/kbo.icns"
cp scripts/launcher/Info.plist "$APP/Contents/Info.plist"
cp scripts/launcher/launch.sh "$APP/Contents/MacOS/launch"
chmod +x "$APP/Contents/MacOS/launch"; touch "$APP"
echo "만들었습니다: $APP"
