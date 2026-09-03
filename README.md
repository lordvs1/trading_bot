# Trading Bot (Bybit USDT Perpetual)

Автоматический торговый бот для криптовалютной биржи **Bybit** (фьючерсы, V5 API).
Торгует `BTCUSDT` и `ETHUSDT` по стратегии на основе EMA / RSI / объёма с
риск-менеджментом (частичные тейки + трейлинг-стоп).

## Возможности

- Тренд-следящая стратегия (EMA200 H1, EMA50/100 M15, RSI14, объём, funding rate)
- Риск-менеджмент: риск 1% на сделку, SL, тейк-профиты +10%/+20% (по 30%), трейлинг на остаток 40%
- Хранение состояния в SQLite — переживает внезапную перезагрузку VPS
- Сверка позиций БД ↔ биржа при старте (расхождения закрываются + уведомление в Telegram)
- Логирование с ротацией (10 MB × 5 файлов): `bot.log`, `errors.log`, `trades.log`
- Telegram-уведомления о входах/выходах/ошибках и ежедневный отчёт
- Health-check: запись `ALIVE` каждые 30 минут
- Корректное завершение по `SIGINT`/`SIGTERM`

## Структура

```
trading_bot/
├── main.py                 # Главный цикл
├── config.py               # Конфигурация из .env
├── requirements.txt
├── .env.example
├── core/
│   ├── bybit_client.py     # Обёртка pybit (REST)
│   ├── strategy.py         # Индикаторы и сигналы
│   ├── risk_manager.py     # Размер позиции, SL/TP, трейлинг
│   ├── state_manager.py    # SQLite: позиции, сделки, статистика
│   └── logging_config.py   # Ротация логов
├── notifier/
│   └── notifier.py         # Telegram-уведомления
├── scripts/
│   ├── restart.sh
│   └── backup_state.sh
├── tests/                  # pytest (TDD)
├── database/               # trades.db (создаётся автоматически)
└── logs/                   # логи (создаётся автоматически)
```

> Папка с Telegram-уведомлениями называется `notifier` (а не `telegram`),
> потому что `python-telegram-bot` сам устанавливает пакет `telegram` — такое
> имя папки конфликтовало бы с библиотекой.

## Установка

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Настройка

```bash
cp .env.example .env
```

Заполните `.env`:

| Переменная           | Описание                              | По умолчанию |
|----------------------|---------------------------------------|--------------|
| `BYBIT_API_KEY`      | API-ключ Bybit                        | —            |
| `BYBIT_API_SECRET`   | Секрет Bybit                          | —            |
| `BYBIT_TESTNET`      | Использовать testnet                  | `True`       |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота                  | —            |
| `TELEGRAM_CHAT_ID`   | ID чата для уведомлений              | —            |
| `TRADE_SYMBOLS`      | Символы (`["BTCUSDT","ETHUSDT"]`)     | BTC, ETH     |
| `MAX_RISK_PER_TRADE` | Риск на сделку (доля баланса)         | `0.01`       |
| `STOP_LOSS_PERCENT`  | Стоп-лосс (доля от цены)              | `0.15`       |
| `LEVERAGE`           | Плечо                                 | `3`          |
| `CHECK_INTERVAL`     | Интервал опроса (секунд)              | `300`        |

## Запуск

```bash
python main.py
```

## Тесты

```bash
pytest tests/ -q
```

## Бэкап и перезапуск (Linux VPS)

```bash
./scripts/backup_state.sh   # копия trades.db и .env в backups/
./scripts/restart.sh        # перезапуск процесса
```

## Примечания

- По умолчанию бот работает на **testnet** (`BYBIT_TESTNET=True`).
- Бот размещает рыночные ордера со стоп-лоссом на бирже; частичные выходы и
  трейлинг выполняются в цикле бота (софт-трейлинг).
- При сбое биржи (недостаточно средств) бот переходит в режим `PAUSE` и
  уведомляет об этом в Telegram.
