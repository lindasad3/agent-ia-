"""Configuration (variables d'environnement préfixées FXA_ ou fichier .env). Aucun secret dans le code."""
from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .data.hf_dataset import DEFAULT_PAIRS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FXA_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Exécution
    broker: str = "paper"  # paper | mt5
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None
    mt5_symbol_suffix: str = ""
    allow_live_account: bool = False  # garde-fou : refuse un compte réel par défaut
    magic: int = 770077

    # Marchés
    symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_PAIRS))
    entry_tf: str = "H1"
    loop_seconds: float = 20.0

    # Modes (valeurs initiales ; modifiables à chaud depuis l'app / Telegram)
    mode: str = "manual"  # manual | auto
    auto_trading: bool = False

    # Modèle
    model_version: str | None = None
    allow_unvalidated_model: bool = False  # démo/paper uniquement : utilise la dernière version même rejetée

    # Telegram
    telegram_token: SecretStr | None = None
    telegram_chat_ids: list[int] = Field(default_factory=list)
    telegram_pin: SecretStr | None = None

    # API / Web
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_user: str = "admin"
    api_password: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    db_url: str = "sqlite:///data/agent.db"

    # Quantower
    quantower_token: SecretStr | None = None
    quantower_symbol_map: dict[str, str] = Field(default_factory=lambda: {
        "EURUSD": "6E", "GBPUSD": "6B", "USDJPY": "6J", "AUDUSD": "6A", "USDCAD": "6C", "USDCHF": "6S", "XAUUSD": "GC"})

    # Calendrier économique
    calendar_enabled: bool = True
    calendar_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    news_block_minutes: int = 30

    # Gestion de position
    be_trigger_r: float = 1.0
    be_lock_r: float = 0.1

    # Paper / replay
    paper_start: str = "2025-03-03T07:00:00Z"
    paper_sim_minutes_per_cycle: int = 60
    paper_balance: float = 10_000.0

    # Sauvegarde
    backup_dir: str = "backups"
    backup_every_hours: float = 24.0
