"""Telegram briefing as card images — the dashboard's own game cards (without the
fold-outs), one image per game, sent as one album per league with the data time.

Rendering: on macOS the system WebKit via scripts/webshot.swift (compiled once into
data/cache — no browser, no screen needed); elsewhere (GitHub Actions) headless
Chrome. If neither works, the text version (one blockquote per game) goes out.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import platform
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from . import notify, report

ROOT = Path(__file__).resolve().parent.parent
SWIFT_SRC = ROOT / "scripts" / "webshot.swift"
CHROME = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
WIDTH = 600       # the dashboard's phone layout (one column below 760 px)
CHROME_H = 500    # Chrome can't measure the page, so it gets a canvas tall enough for one card
EXTRA_CSS = """
body{background:var(--bg)}
.sheet{width:600px;padding:14px 14px 12px}
.sheet .game{margin:0}
.cap{display:flex;justify-content:space-between;align-items:baseline;margin:0 2px 8px;font:500 13px/1 var(--body);color:var(--ink-2)}
.cap b{font:700 18px/1 var(--display);color:var(--ink);letter-spacing:.02em}
.sheet .stamp{margin:8px 2px 0;font-size:11.5px}
"""


def _title(day: dict) -> str:
    d = dt.date.fromisoformat(day["date"])
    return f"{d.month}월 {d.day}일 ({report.WEEKDAYS[d.weekday()]})"


def card_doc(day: dict, r: dict) -> str:
    e = html.escape
    return (f'<!doctype html><html data-theme="light"><meta charset="utf-8">{report.FONT_LINK}'
            f'<style>{report.CSS}{EXTRA_CSS}</style><div class="sheet">'
            f'<div class="cap"><b>{e(_title(day))}</b><span>KBO {e(r["league_name"])}</span></div>'
            f'{report.game_card(r, folds=False)}<p class="stamp">{e(report.data_stamp(day))}</p></div></html>')


def find_chrome() -> str | None:
    return next((p for c in CHROME if (p := shutil.which(c))), None)


def _webshot() -> str | None:
    """Compile scripts/webshot.swift once (macOS only); rebuild when the source changes."""
    if platform.system() != "Darwin" or not shutil.which("swiftc"):
        return None
    exe = ROOT / "data" / "cache" / "webshot"
    if not exe.exists() or exe.stat().st_mtime < SWIFT_SRC.stat().st_mtime:
        exe.parent.mkdir(parents=True, exist_ok=True)
        if subprocess.run(["swiftc", "-O", "-swift-version", "5", "-o", str(exe), str(SWIFT_SRC)],
                          capture_output=True, timeout=300).returncode != 0:
            return None
    return str(exe)


def _run(cmd: list[str], timeout: int) -> None:
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)   # Chrome leaves helper processes; take the whole group
        proc.wait()
        raise RuntimeError(f"{Path(cmd[0]).name}: {timeout}초 안에 끝나지 않음")
    if code != 0:
        raise RuntimeError(f"{Path(cmd[0]).name}: 종료 코드 {code}")


def render(doc: str, out_png: Path) -> Path:
    out_png.unlink(missing_ok=True)
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "card.html"
        src.write_text(doc, "utf-8")
        if exe := _webshot():
            try:
                _run([exe, str(src), str(out_png), str(WIDTH), "2"], 40)
            except RuntimeError as e:
                errors.append(str(e))
        if not out_png.exists() and (chrome := find_chrome()):
            try:
                _run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                      "--no-first-run", "--use-mock-keychain", f"--user-data-dir={tmp}/profile",
                      "--force-device-scale-factor=2", f"--window-size={WIDTH},{CHROME_H}",
                      "--virtual-time-budget=5000", f"--screenshot={out_png}", src.as_uri()], 40)
            except RuntimeError as e:
                errors.append(str(e))
    if not out_png.exists() or out_png.stat().st_size < 2000:
        raise RuntimeError("; ".join(errors) or "카드 이미지를 만들 도구가 없음 (WebKit·Chrome)")
    return out_png


def make(day: dict, reports: list[dict], out_dir: Path) -> list[Path]:
    """One PNG per game: the dashboard card without its fold-outs."""
    return [render(card_doc(day, r), (out_dir / f"{day['date']}-card-{i:02d}-{r['stadium']['key']}.png").resolve())
            for i, r in enumerate(reports, 1)]


def caption(day: dict, rs: list[dict], name: str) -> str:
    return (f"⚾ <b>{html.escape(_title(day))} KBO {name}</b> · {len(rs)}경기\n"
            f"{html.escape(report.league_summary(rs).split(' · ', 1)[-1])}\n"
            f"<i>{html.escape(report.data_stamp(day))}</i>")


def send_briefing(s, day: dict, reports: list[dict] | None = None, story: str | None = None) -> str:
    """Render every card first, then send one album per league; text version if rendering fails."""
    reports = day["reports"] if reports is None else reports
    if not reports:
        return "보낼 경기 없음"
    try:
        albums = []
        for le, name, _ in report.LEAGUES:
            rs = [r for r in reports if r["game"]["league"] == le]
            if rs:
                albums.append((make(day, rs, s.out_dir), caption(day, rs, name)))
    except Exception as e:  # never lose the briefing over a rendering problem
        notify.telegram(s.telegram_token, s.telegram_chat_id, report.to_telegram_html({**day, "reports": reports}))
        return f"텔레그램 텍스트 전송 (카드 실패: {str(e).splitlines()[0][:80]})"
    if story:
        albums[0] = (albums[0][0], "🎙 " + html.escape(story[:400]) + "\n\n" + albums[0][1])
    for pngs, cap in albums:
        for i in range(0, len(pngs), 10):          # an album holds at most 10 photos
            notify.telegram_photos(s.telegram_token, s.telegram_chat_id, pngs[i:i + 10], cap[:1024] if i == 0 else "")
    return f"텔레그램 카드 {sum(len(p) for p, _ in albums)}장 전송"
