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


class TestTradeMode:
    def test_default_is_live(self):
        cfg = Config(api_key="k", api_secret="s")
        assert cfg.trade_mode == "live"

    def test_loaded_from_env(self, tmp_path):
        env = _write_env(tmp_path, "TRADE_MODE=paper\n")
        cfg = Config.load(env)
        assert cfg.trade_mode == "paper"


class TestInitialBalance:
    def test_default(self):
        assert Config().initial_balance == pytest.approx(1000.0)

    def test_loaded_from_env(self, tmp_path):
        env = _write_env(tmp_path, "INITIAL_BALANCE=250\n")
        cfg = Config.load(env)
        assert cfg.initial_balance == pytest.approx(250.0)


class TestSizingConfig:
    def test_defaults(self):
        cfg = Config()
        assert cfg.size_to_min_lot is False
        assert cfg.risk_hard_cap == pytest.approx(0.03)

    def test_loaded_from_env(self, tmp_path):
        env = _write_env(tmp_path, "SIZE_TO_MIN_LOT=False\nRISK_HARD_CAP=0.05\n")
        cfg = Config.load(env)
        assert cfg.size_to_min_lot is False
        assert cfg.risk_hard_cap == pytest.approx(0.05)


class TestSymbolOverrides:
    def test_params_and_risk_for(self, tmp_path):
        env = _write_env(
            tmp_path,
            'SYMBOL_OVERRIDES={"BTCUSDT": {"max_stop_pct": 0.015, "max_risk_per_trade": 0.02}}\n',
        )
        cfg = Config.load(env)
        assert cfg.params_for("BTCUSDT") == {"max_stop_pct": 0.015}
        assert cfg.risk_for("BTCUSDT") == pytest.approx(0.02)
        assert cfg.params_for("ETHUSDT") == {}
        assert cfg.risk_for("ETHUSDT") == pytest.approx(cfg.max_risk_per_trade)

    def test_invalid_json_is_ignored(self, tmp_path):
        env = _write_env(tmp_path, "SYMBOL_OVERRIDES=not-json\n")
        cfg = Config.load(env)
        assert cfg.symbol_overrides == {}


class TestTelegram:
    def test_enabled_flag(self):
        assert Config(telegram_bot_token="t", telegram_chat_id="1").telegram_enabled is True
        assert Config(telegram_bot_token="", telegram_chat_id="1").telegram_enabled is False

    def test_balance_change_pct_default(self):
        assert Config().balance_change_pct == pytest.approx(0.005)


class TestValidate:
    def test_live_requires_keys(self):
        cfg = Config(api_key="", api_secret="")
        with pytest.raises(ValueError):
            cfg.validate()

    def test_live_rejects_placeholder_keys(self):
        cfg = Config(api_key="your_api_key_here", api_secret="s")
        with pytest.raises(ValueError):
            cfg.validate()

    def test_paper_does_not_require_keys(self):
        cfg = Config(api_key="", api_secret="", trade_mode="paper")
        cfg.validate()

    def test_unknown_trade_mode_rejected(self):
        cfg = Config(api_key="k", api_secret="s", trade_mode="banana")
        with pytest.raises(ValueError):
            cfg.validate()
