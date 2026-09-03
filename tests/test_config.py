import pytest

from config import Config


def _write_env(tmp_path, content):
    p = tmp_path / ".env"
    p.write_text(content, encoding="utf-8")
    return p


class TestConfigLoad:
    def test_loads_all_fields(self, tmp_path):
        env = _write_env(
            tmp_path,
            """
            BYBIT_API_KEY=abc123
            BYBIT_API_SECRET=secret456
            BYBIT_TESTNET=False
            TELEGRAM_BOT_TOKEN=111:tok
            TELEGRAM_CHAT_ID=123456
            TRADE_SYMBOLS=["BTCUSDT","ETHUSDT"]
            MAX_RISK_PER_TRADE=0.02
            STOP_LOSS_PERCENT=0.10
            LEVERAGE=5
            CHECK_INTERVAL=60
            """,
        )
        cfg = Config.load(env)

        assert cfg.api_key == "abc123"
        assert cfg.api_secret == "secret456"
        assert cfg.testnet is False
        assert cfg.telegram_bot_token == "111:tok"
        assert cfg.telegram_chat_id == "123456"
        assert cfg.trade_symbols == ["BTCUSDT", "ETHUSDT"]
        assert cfg.max_risk_per_trade == pytest.approx(0.02)
        assert cfg.stop_loss_percent == pytest.approx(0.10)
        assert cfg.leverage == 5
        assert cfg.check_interval == 60

    def test_defaults_when_env_missing(self, tmp_path):
        cfg = Config.load(tmp_path / "missing.env")
        assert cfg.testnet is True
        assert cfg.trade_symbols == ["BTCUSDT", "ETHUSDT"]
        assert cfg.max_risk_per_trade == pytest.approx(0.01)
        assert cfg.stop_loss_percent == pytest.approx(0.15)
        assert cfg.leverage == 3
        assert cfg.check_interval == 300


class TestParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("True", True),
            ("true", True),
            ("1", True),
            ("False", False),
            ("false", False),
            ("0", False),
        ],
    )
    def test_bool_parsing(self, raw, expected):
        assert Config._parse_bool(raw) is expected

    def test_list_from_json(self):
        assert Config._parse_list('["BTCUSDT", "ETHUSDT"]') == ["BTCUSDT", "ETHUSDT"]

    def test_list_from_comma_separated(self):
        assert Config._parse_list("BTCUSDT,ETHUSDT") == ["BTCUSDT", "ETHUSDT"]

    def test_list_single_item(self):
        assert Config._parse_list("BTCUSDT") == ["BTCUSDT"]


class TestBaseUrl:
    def test_testnet_url(self):
        cfg = Config(api_key="k", api_secret="s", testnet=True)
        assert cfg.base_url == "https://api-testnet.bybit.com"

    def test_mainnet_url(self):
        cfg = Config(api_key="k", api_secret="s", testnet=False)
        assert cfg.base_url == "https://api.bybit.com"
