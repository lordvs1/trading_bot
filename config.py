"""Application configuration loaded from environment variables (.env)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import dotenv_values

TESTNET_BASE_URL = "https://api-testnet.bybit.com"
MAINNET_BASE_URL = "https://api.bybit.com"

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


@dataclass
class Config:
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    trade_symbols: list = field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    max_risk_per_trade: float = 0.01
    stop_loss_percent: float = 0.15
    leverage: int = 3
    check_interval: int = 300
    trade_mode: str = "live"
    strategy: str = "trend_ema"
    initial_balance: float = 1000.0
    size_to_min_lot: bool = False
    risk_hard_cap: float = 0.03
    symbol_overrides: dict = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return TESTNET_BASE_URL if self.testnet else MAINNET_BASE_URL

    def validate(self) -> "Config":
        """Fail fast on an unusable configuration.

        API keys are only required in live mode; paper/backtest modes run on
        public market data without credentials.
        """
        if self.trade_mode not in ("live", "paper"):
            raise ValueError(
                f"Unknown TRADE_MODE '{self.trade_mode}' (expected 'live' or 'paper')"
            )
        if self.trade_mode == "live":
            if not self.api_key or not self.api_secret:
                raise ValueError(
                    "BYBIT_API_KEY and BYBIT_API_SECRET are required in live mode"
                )
            if "your_" in self.api_key or "your_" in self.api_secret:
                raise ValueError(
                    "BYBIT_API_KEY / BYBIT_API_SECRET still contain placeholder values"
                )
        return self

    @classmethod
    def load(cls, env_path: Optional[str] = None) -> "Config":
        """Load configuration from a .env file (or the process environment).

        Values are read without mutating the process environment so that the
        config stays deterministic and side-effect free across tests.
        """
        path = env_path or ".env"
        file_values = dotenv_values(path)  # {} when the file does not exist
        merged = {**os.environ, **file_values}
        return cls.from_env(merged)

    @classmethod
    def from_env(cls, env) -> "Config":
        return cls(
            api_key=env.get("BYBIT_API_KEY", ""),
            api_secret=env.get("BYBIT_API_SECRET", ""),
            testnet=cls._parse_bool(env.get("BYBIT_TESTNET", "True")),
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID", ""),
            trade_symbols=cls._parse_list(env.get("TRADE_SYMBOLS", "")),
            max_risk_per_trade=float(env.get("MAX_RISK_PER_TRADE", "0.01")),
            stop_loss_percent=float(env.get("STOP_LOSS_PERCENT", "0.15")),
            leverage=int(env.get("LEVERAGE", "3")),
            check_interval=int(env.get("CHECK_INTERVAL", "300")),
            trade_mode=env.get("TRADE_MODE", "live").strip().lower(),
            strategy=env.get("STRATEGY", "trend_ema").strip().lower(),
            initial_balance=float(env.get("INITIAL_BALANCE", "1000")),
            size_to_min_lot=cls._parse_bool(env.get("SIZE_TO_MIN_LOT", "False")),
            risk_hard_cap=float(env.get("RISK_HARD_CAP", "0.03")),
            symbol_overrides=cls._parse_json_dict(env.get("SYMBOL_OVERRIDES", "")),
        )

    def params_for(self, symbol: str) -> dict:
        """Strategy-parameter overrides for a symbol (excluding the risk key)."""
        ov = self.symbol_overrides.get(symbol, {})
        return {k: v for k, v in ov.items() if k != "max_risk_per_trade"}

    def risk_for(self, symbol: str) -> float:
        ov = self.symbol_overrides.get(symbol, {})
        return float(ov.get("max_risk_per_trade", self.max_risk_per_trade))

    @staticmethod
    def _parse_bool(value: str) -> bool:
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _parse_list(value: str) -> list:
        """Parse TRADE_SYMBOLS from a JSON array or a comma-separated string."""
        value = (value or "").strip()
        if not value:
            return list(DEFAULT_SYMBOLS)
        if value.startswith("["):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except json.JSONDecodeError:
                pass
        return [x.strip().upper() for x in value.split(",") if x.strip()]

    @staticmethod
    def _parse_json_dict(value: str) -> dict:
        value = (value or "").strip()
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
