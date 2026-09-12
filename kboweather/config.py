"""Settings: config.toml (optional) + environment variables. Zero required."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    cache_dir: Path = ROOT / "data" / "cache"
    out_dir: Path = ROOT / "out"
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    dashboard_url: str = "https://woojeongryeol.github.io/kbo-weather-forecast/"   # 텔레그램 카드 아래 링크
    telegram_leagues: tuple[int, ...] = (1,)   # 텔레그램으로 보낼 리그 (1군만; 퓨처스까지 보내려면 (1, 2))
    google_weather_key: str | None = None      # optional: WeatherNext 3 via Maps Platform Weather API
    kma_service_key: str | None = None         # optional: 기상청 단기·초단기예보 (공공데이터포털 일반 인증키)
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "gemma4:12b"
    tts_voice: str = "Yuna"
    game_length_h: float = 3.5
    forecast_days: int = 3


ENV = {
    "telegram_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "telegram_leagues": "TELEGRAM_LEAGUES",
    "dashboard_url": "KBO_DASHBOARD_URL",
    "google_weather_key": "GOOGLE_WEATHER_API_KEY",
    "kma_service_key": "KMA_SERVICE_KEY",
    "ollama_host": "OLLAMA_HOST",
    "ollama_model": "OLLAMA_MODEL",
    "tts_voice": "KBO_TTS_VOICE",
}


def load_settings(path: Path | None = None) -> Settings:
    s = Settings()
    cfg = path or ROOT / "config.toml"
    if cfg.exists():
        for k, v in tomllib.loads(cfg.read_text("utf-8")).items():
            if hasattr(s, k):
                setattr(s, k, v)
    for attr, var in ENV.items():
        if os.environ.get(var):
            setattr(s, attr, os.environ[var])
    if isinstance(s.telegram_leagues, str):        # env var: "1" or "1,2"
        s.telegram_leagues = tuple(int(x) for x in s.telegram_leagues.replace(" ", "").split(",") if x)
    else:
        s.telegram_leagues = tuple(int(x) for x in s.telegram_leagues)
    s.cache_dir = Path(s.cache_dir)
    s.out_dir = Path(s.out_dir)
    s.cache_dir.mkdir(parents=True, exist_ok=True)
    s.out_dir.mkdir(parents=True, exist_ok=True)
    return s
