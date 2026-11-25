"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

Biến môi trường (liệt kê đủ trong .env.example):
    PORT, ENVIRONMENT, LOG_LEVEL, AGENT_API_KEY, REDIS_URL,
    RATE_LIMIT_PER_MINUTE, DAILY_BUDGET_USD, MONTHLY_BUDGET_USD,
    HISTORY_MAX_MESSAGES

Không có secret nào được hard-code: AGENT_API_KEY mặc định rỗng, khi rỗng
mọi request tới /ask đều bị từ chối (401) thay vì "mở cửa".
"""
import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: _env_str("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: _env_str("LOG_LEVEL", "INFO").upper())
    agent_api_key: str = field(default_factory=lambda: _env_str("AGENT_API_KEY", ""))
    redis_url: str = field(default_factory=lambda: _env_str("REDIS_URL", "redis://localhost:6379/0"))
    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))
    monthly_budget_usd: float = field(default_factory=lambda: _env_float("MONTHLY_BUDGET_USD", 10.0))
    history_max_messages: int = field(default_factory=lambda: _env_int("HISTORY_MAX_MESSAGES", 20))


settings = Settings()
