"""Delivery: Telegram bot message, macOS voice briefing (say → .m4a)."""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
import uuid
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


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "kboweather" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode(), str(v).encode(), b"\r\n"]
    for k, (name, data) in files.items():
        parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                  "Content-Type: image/png\r\n\r\n".encode(), data, b"\r\n"]
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def telegram_photos(token: str, chat_id: str, photos: list[Path], caption_html: str = "", timeout: int = 90) -> dict:
    """One photo → sendPhoto; several → one album (sendMediaGroup) with the caption on the first."""
    if len(photos) == 1:
        method = "sendPhoto"
        body, ctype = _multipart({"chat_id": chat_id, "caption": caption_html, "parse_mode": "HTML"},
                                 {"photo": (photos[0].name, photos[0].read_bytes())})
    else:
        method = "sendMediaGroup"
        media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(photos))]
        if caption_html:
            media[0].update(caption=caption_html, parse_mode="HTML")
        body, ctype = _multipart({"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)},
                                 {f"p{i}": (p.name, p.read_bytes()) for i, p in enumerate(photos)})
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body,
                                 headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


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
