"""Delivery: Telegram bot message, macOS voice briefing (say → .m4a)."""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from pathlib import Path


def telegram(token: str, chat_id: str, html_text: str, timeout: int = 20) -> list[dict]:
    """Send (chunked at 3900 chars) with HTML parse mode."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks, cur = [], ""
    for line in html_text.splitlines(keepends=True):
        if len(cur) + len(line) > 3900:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    results = []
    for c in chunks:
        body = json.dumps({"chat_id": chat_id, "text": c, "parse_mode": "HTML",
                           "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            results.append(json.load(r))
    return results


def speak(text: str, out_path: Path, voice: str = "Yuna") -> Path | None:
    """macOS only: render a voice briefing to .m4a (falls back to .aiff)."""
    if not shutil.which("say"):
        return None
    aiff = out_path.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    if shutil.which("afconvert"):
        m4a = out_path.with_suffix(".m4a")
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(m4a)], check=True)
        aiff.unlink(missing_ok=True)
        return m4a
    return aiff
