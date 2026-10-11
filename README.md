# Trading Bot (Bybit USDT Perpetual)

Автоматический торговый бот для фьючерсов **Bybit** (V5 API, USDT Perpetual).
Поддерживает переключаемые стратегии: `trend_ema` (тренд-следящая, заморожена)
и `range` (торговля в боковике от уровня к уровню). Есть бэктест, диагностика
сигналов, перебор параметров и walk-forward валидация — всё работает на
публичных данных Bybit **без API-ключей**.

## Возможности

- Переключаемые стратегии (реестр `strategies/`, выбор через `STRATEGY`).
- Риск-менеджмент: риск на сделку, стоп-лосс, частичные тейки / трейлинг (у trend) или одиночные TP/SL по уровням (у range).
- Хранение состояния в SQLite — переживает внезапную перезагрузку VPS.
- Сверка позиций БД ↔ биржа (расхождения закрываются + уведомление в Telegram).
- Логирование с ротацией (10 MB × 5 файлов): `bot.log`, `errors.log`, `trades.log`.
- Telegram-уведомления о входах/выходах/ошибках и ежедневный отчёт.
- Бэктест / диагностика / перебор параметров / walk-forward на публичных данных.
- Учёт минимального лота и автопропуск сделок, которые не влезают в бюджет.
- Health-check `ALIVE` каждые 30 минут; корректное завершение по `SIGINT`/`SIGTERM`.

## Установка

### Чистая Ubuntu (включая 26.04) — одной командой

```bash
sudo apt-get update && sudo apt-get install -y git
git clone <your-repo-url> trading_bot && cd trading_bot
sudo bash scripts/setup.sh
```

`scripts/setup.sh` ставит системные пакеты (`git python3 python3-venv python3-pip
python3-dev build-essential`), (пере)создаёт `venv/`, устанавливает зависимости и
проверяет импорты. Идемпотентен — можно запускать повторно.

### Ручная установка (Linux/macOS/Windows)

```bash
sudo apt-get install -y python3 python3-venv python3-pip   # Debian/Ubuntu (Linux)
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Если `python3 -m venv` падает (ensurepip)

На Debian/Ubuntu `venv` ставится отдельным пакетом. Ошибка вида
`ensurepip is not available` означает, что его нет:

```bash
sudo apt-get install -y python3-venv python3-pip
# для конкретной версии, напр. Python 3.14:  sudo apt-get install -y python3.14-venv
rm -rf venv            # удалить «полусозданный» venv
python3 -m venv venv
```

### Если Python слишком новый (3.13/3.14) или нет колёс pandas/numpy

`pip` попытается собрать `pandas`/`numpy` из исходников. Проект рассчитан на
**Python 3.10–3.12**. Варианты:
- поставить 3.12 из apt (`sudo apt-get install -y python3.12 python3.12-venv`) и использовать `python3.12 -m venv venv`;
- либо `uv` (самодостаточный, ставит Python без apt):
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  uv venv --python 3.12
  uv pip install -r requirements.txt
  ```

> На новых Ubuntu `pip install` в систему запрещён (PEP 668, `externally-managed-environment`) — поэтому используйте виртуальное окружение.


## Настройка (.env)

```bash
cp .env.example .env
```

| Переменная           | Описание                              | По умолчанию |
|----------------------|---------------------------------------|--------------|
| `BYBIT_API_KEY`      | API-ключ Bybit                        | —            |
| `BYBIT_API_SECRET`   | Секрет Bybit                          | —            |
| `BYBIT_TESTNET`      | Использовать testnet                  | `True`       |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота                  | —            |
| `TELEGRAM_CHAT_ID`   | ID чата для уведомлений              | —            |
| `TRADE_SYMBOLS`      | Символы (`["BTCUSDT","ETHUSDT"]`)     | BTC, ETH     |
| `MAX_RISK_PER_TRADE` | Риск на сделку (доля баланса)         | `0.01`       |
| `STOP_LOSS_PERCENT`  | Стоп-лосс по умолчанию (для trend)    | `0.15`       |
| `LEVERAGE`           | Плечо                                 | `3`          |
| `CHECK_INTERVAL`     | Интервал опроса (секунд)              | `300`        |
| `TRADE_MODE`         | `live` или `paper`                    | `live`       |
| `STRATEGY`           | Стратегия: `trend_ema` или `range`    | `trend_ema`  |
| `INITIAL_BALANCE`    | Стартовый баланс (paper/бэктест)      | `1000`       |
| `SIZE_TO_MIN_LOT`    | Добирать лот до мин-лота (см. ниже)   | `False`      |
| `RISK_HARD_CAP`      | Жёсткий предел риска для добора        | `0.03`       |
| `SYMBOL_OVERRIDES`   | Переопределения по символам (JSON)     | `{}`         |

`SYMBOL_OVERRIDES` — JSON, напр.:
`{"BTCUSDT": {"max_stop_pct": 0.015, "max_risk_per_trade": 0.015}, "ETHUSDT": {"max_stop_pct": 0.03}}`.
Ключ `max_risk_per_trade` — это риск для символа; остальные ключи — параметры стратегии.

## Структура

```
trading_bot/
├── main.py                 # Главный цикл (live/paper)
├── config.py               # Конфигурация из .env (Config, validate())
├── backtest.py             # CLI: backtest / diagnose / sweep / walk-forward
├── strategies/             # Стратегии (переключаемые)
│   ├── __init__.py         # Реестр STRATEGIES, get_strategy()
│   ├── base.py             # StrategyBase (общий интерфейс)
│   ├── trend_ema.py        # Тренд-следящая (заморожена)
│   └── range_trading.py    # Боковик: ADX + пивоты, от уровня к уровню
├── core/
│   ├── indicators.py       # ema/sma/rsi/adx/atr/pivots
│   ├── bybit_client.py     # Обёртка pybit (REST, live)
│   ├── paper_broker.py     # Симулированный брокер (paper-режим)
│   ├── position_manager.py # Выход trend: частичные тейки + трейлинг
│   ├── level_position.py   # Выход range: одиночные SL/TP по уровням
│   ├── risk_manager.py     # Размер позиции, SL/TP, трейлинг
│   ├── state_manager.py    # SQLite: positions / trades / daily_stats
│   └── logging_config.py   # Ротация логов
├── backtest/
│   ├── data_loader.py      # Загрузка истории (публичные эндпоинты, CSV-кэш)
│   ├── engine.py           # Бэктест-движок (стратегия-независимый)
│   └── sweep.py            # Перебор параметров + walk-forward
├── notifier/notifier.py    # Telegram-уведомления
├── scripts/
│   ├── fetch_data.py       # Скачивание исторических данных
│   ├── restart.sh
│   └── backup_state.sh
├── tests/                  # pytest (TDD)
├── data/                   # CSV-кэш исторических данных (общий для стратегий)
├── reports/                # CSV-отчёты бэктеста
├── database/               # trades.db (создаётся автоматически)
└── logs/                   # логи (создаётся автоматически)
```

> Папка с Telegram-уведомлениями называется `notifier` (а не `telegram`),
> потому что `python-telegram-bot` сам устанавливает пакет `telegram`.

## Стратегии

### `trend_ema` (заморожена)

Тренд-следящая: EMA200 (H1), EMA50/100 (M15), RSI14, объём, funding rate.
Выход — частичные тейки (+tp1%/+tp2%) + трейлинг.

**Итоги бэктеста (12 мес, публичные данные, BTC+ETH):** полный бэктест ETH дал
profit factor 0.72; walk-forward (4 фолда) — in-sample PF 1.6–1.9 **схлопывается
до out-of-sample ≈ 1.0**. Статистически значимого края нет — не использовать в
бою. Оставлена как референс.

### `range` (боковик, новая)

Активируется только в боковике (ADX < порога), находит support/resistance как
пивоты H1 и торгует от уровня к уровню: вход у support (LONG) / у resistance
(SHORT), стоп за уровнем (кратно ATR), тейк на противоположном уровне.

**Итоги walk-forward (12 мес, 4 фолда, риск 1%, только BTC и ETH):**

| Счёт | OOS PF | OOS темп/год | сдел. | DD |
|------|--------|--------------|-------|----|
| BTC  | 4.16   | **≈ +11%**   | 9     | −3.4% |
| ETH  | 3.56   | **≈ +9%**    | 8     | −2.2% |

Край **сохраняется** вне выборки (PF 3.5–4.2), но частота сделок мала, поэтому
годовой OOS-темп ≈ 9–11% на счёт. Цель **+20% OOS при DD ≤ 5% на BTC+ETH не
подтверждается**: чтобы добрать в 2×, риск надо поднять до ~2%, а просадка выходит
за 5%. In-sample +13% на BTC — это в основном удача периода. Вывод: на паре
BTC+ETH устойчиво доступно ~10%/год; для 20% нужны доп. источники (символы/история).

Параметры:

| Параметр          | Описание                                    | По умолчанию |
|-------------------|---------------------------------------------|--------------|
| `adx_period`      | Период ADX (H1)                             | `14`         |
| `adx_threshold`   | Ниже — рынок считается боковым              | `25`         |
| `pivot_lookback`  | Окно поиска ближайшего S/R (баров H1)       | `20`         |
| `pivot_confirm`   | Баров с каждой стороны для подтверждения пивота | `2`      |
| `entry_buffer`    | Близость к уровню для входа (доля цены)     | `0.002`      |
| `atr_period`      | Период ATR (H1)                             | `14`         |
| `sl_atr_mult`     | Стоп за уровнем, кратно ATR                 | `2.0`        |
| `min_risk_reward` | Мин. соотношение риск/прибыль для входа     | `2.0`        |
| `min_range_width` | Мин. ширина диапазона (доля)                | `0.01`       |
| `max_range_width` | Макс. ширина диапазона (доля)               | `0.08`       |
| `entry_order_type`| Вход: `limit` (лимитка на уровне) или `market` | `market`   |
| `limit_valid_bars`| Сколько баров ждать исполнения лимитки      | `8`          |
| `max_stop_pct`    | Отсев сигналов с широким стопом (доля)      | `None` (выкл) |

> Стоп привязан к ATR (не фиксированная доля), а вход требует `min_risk_reward`.
> Это убирает «шумовые» стопы, которые были у первой версии. Дефолты — стартовая
> точка, подбирайте через `--sweep` / `--walk-forward`.
>
> При `entry_order_type=limit` вход ставится лимиткой по цене уровня (maker), тейк
> на противоположном уровне — тоже лимитка (maker), стоп — taker. Часть заявок может
> не исполниться (`Unfilled limit orders`) — это цена экономии на комиссиях.
> Сравнить режимы: `--set entry_order_type=market`.

## Запуск бота

```bash
python main.py
```

Режимы (`TRADE_MODE`):
- `live` — реальная торговля (нужны API-ключи).
- `paper` — симуляция на живых публичных котировках **без ключей и ордеров** (виртуальный баланс).

## Тестирование без API-ключей

```bash
python scripts/fetch_data.py --months 12   # скачать H1+M15+funding в data/
python backtest.py --strategy range --months 12                       # бэктест + CSV-отчёт
python backtest.py --strategy range --diagnose                        # воронка условий сигнала
python backtest.py --strategy range --sweep --months 12               # перебор параметров
python backtest.py --strategy range --walk-forward --folds 4 --months 12  # без переобучения
```

### Флаги `backtest.py`

| Флаг | Смысл | Значения | По умолчанию |
|------|-------|----------|--------------|
| `--symbols` | Список символов через запятую | любые (`BTCUSDT,ETHUSDT`) | `BTCUSDT,ETHUSDT` |
| `--strategy` | Какую стратегию запускать | `trend_ema`, `range` | из `STRATEGY` в `.env` |
| `--months` | Глубина истории | целое | `6` |
| `--data-dir` | Папка CSV-кэша данных | путь | `data` |
| `--report-dir` | Папка CSV-отчётов | путь | `reports` |
| `--initial-balance` | Стартовый баланс каждого прогона | число | `INITIAL_BALANCE` из `.env`, иначе `1000` |
| `--taker-fee` | Комиссия тейкера (market/стопы) | доля | `0.00055` |
| `--maker-fee` | Комиссия мейкера (лимитки/тейк) | доля | `0.0002` |
| `--risk` | Риск на сделку (переопределяет `MAX_RISK_PER_TRADE`) | доля | из `.env` |
| `--no-topup` | Отключить добор лота до мин-лота | флаг | — |
| `--max-dd` | Отсеивать конфиги с просадкой хуже порога | доля (напр. `0.05`) | без фильтра |
| `--set KEY=VALUE` | Переопределить параметр стратегии (можно несколько раз) | напр. `--set entry_order_type=market` | — |
| `--objective` | Объектив ранжирования sweep/walk-forward | `pf`, `return`, `calmar` | `pf` |
| `--diagnose` | Режим: воронка условий сигнала | флаг | выкл |
| `--sweep` | Режим: перебор параметров | флаг | выкл |
| `--walk-forward` | Режим: walk-forward валидация | флаг | выкл |
| `--folds` | Число out-of-sample окон (walk-forward) | целое ≥ 2 | `3` |
| `--min-positions` | Мин. сделок **in-sample**, чтобы оставить набор (якорь) | целое | `15` |
| `--min-oos-positions` | Мин. **out-of-sample** сделок, чтобы оставить набор | целое | `10` |
| `--top` | sweep: сколько показать; walk-forward: сколько якорей | целое | `10` |

> `--initial-balance` переопределяет `INITIAL_BALANCE` из `.env`. `--top` означает
> разное в двух режимах (см. выше). Все флаги актуальны.

### Как читать walk-forward

- Каждый фолд — **независимое** окно (~`months/folds`), стартует заново с `--initial-balance`.
- `OOS PnL` — **сумма** PnL по всем фолдам и символам (не компаундится).
- `pos` / `OOS PF` — число сделок и профит-фактор **вне выборки** (главный показатель).
- `In-sample (full period)` — прогон этих же параметров на всей истории (с смещением
  отбора, т.к. параметры выбирались на ней же) — для справки и «итогового баланса».
- Набор считается перспективным, если `OOS PF` заметно > 1 и не сильно ниже in-sample.

**Если таблица walk-forward пустая** — это **не** «стратегия ничего не сделала» и
не «0 PnL», а сработал фильтр по размеру выборки (`--min-oos-positions`). Тогда
выводится диагностика: максимальное число OOS-сделок среди якорей и рекомендованный
порог, плюс ближайшие наборы с пометкой `[below threshold]`.

Что делать:
- Понизить `--min-oos-positions` до рекомендованного значения.
- Поднять `--min-positions` (напр. `15`), чтобы якоря отбирались по наборам с
  достаточным числом сделок (меньше шума, больше OOS-сделок).
- Увеличить `--top`, историю (`--months`) или число символов (`--symbols`).
- Или снизить избирательность стратегии (для `range` — `min_risk_reward`/`entry_buffer`).

Данные (`data/*.csv`) — общий кэш для **любой** стратегии (H1/M15/funding), повторно
скачивать не нужно. Отчёт бэктеста сохраняется в `reports/<SYMBOL>_<STRATEGY>_trades.csv`.

## Бюджет ~$100

- Позиция считается от **фактического** стопа (вход → уровень), риск фиксирован.
- Номинал ограничен доступной маржой (`balance × leverage`).
- Если расчётный лот меньше минимального лота биржи — сделка **пропускается**
  (в отчёте — счётчик `Skipped (min lot)`). Для $100 практичнее ETH
  (мин-лот 0.01 ETH), BTC (0.001 BTC) часто не влезает.
- `SIZE_TO_MIN_LOT=True` включает «добор» лота до минимума биржи (в пределах
  `RISK_HARD_CAP`). **Замер показал, что на BTC это вредит**: форсирует маржинальные
  сделки, доход BTC падает, просадка растёт. Поэтому по умолчанию **выключено**;
  включать только осознанно (`SIZE_TO_MIN_LOT=True` или флаг без `--no-topup`).
- Риск под цель задаётся `MAX_RISK_PER_TRADE` (или флагом `--risk`): доход
  масштабируется примерно линейно, так же растёт просадка.

### Про реалистичный доход (важно)
In-sample прогон на BTC+ETH (12 мес, риск 1.5%) даёт ~+23%. Но **walk-forward
out-of-sample годовой темп заметно ниже** (~единицы-десятки %): часть in-sample
результата — удача периода. Прежде чем рассчитывать на 20%+, смотрите именно
`OOS`-строки walk-forward, а не in-sample.

## Telegram-команды

Бот отвечает только на `TELEGRAM_CHAT_ID` владельца:

| Команда | Что делает |
|---------|------------|
| `/balance` (`/status`) | баланс, режим, стратегия, открытые позиции, PnL за день |
| `/positions` | список открытых позиций |
| `/stats` | дневная статистика (PnL, сделки, win rate) |
| `/strategy` | показать текущую стратегию |
| `/strategy range` \| `/strategy trend_ema` | сменить стратегию (runtime) |
| `/pause` / `/resume` | пауза/возобновление **новых входов** |
| `/help` | справка |

Плюс автоматически: уведомления о запуске, о входе/выходе (с балансом),
об ошибках, ежедневный отчёт в 23:50 и сообщение при изменении баланса
на ≥ `BALANCE_CHANGE_PCT` (по умолч. 0.5%).

> Смена стратегии — **runtime**: после перезапуска берётся `STRATEGY` из `.env`.
> Уже открытые позиции продолжают вестись стратегией, с которой были открыты.

## Развёртывание на VPS (systemd)

```bash
# 1. Клонировать и установить зависимости
sudo apt-get update && sudo apt-get install -y git
cd /opt && sudo git clone <repo> trading_bot && cd trading_bot
sudo bash scripts/setup.sh        # ставит venv/pip и зависимости, проверяет импорты

# 2. Настроить .env (бумажная торговля, ключи Bybit не нужны)
cp .env.example .env
nano .env
#   TRADE_MODE=paper
#   INITIAL_BALANCE=100
#   STRATEGY=range
#   TELEGRAM_BOT_TOKEN=...
#   TELEGRAM_CHAT_ID=...

# 3. Часовой пояс (для дневного отчёта в 23:50)
sudo timedatectl set-timezone Europe/Moscow   # или ваш город
```

Как узнать `TELEGRAM_CHAT_ID`: отправьте боту любое сообщение и откройте
`https://api.telegram.org/bot<TOKEN>/getUpdates` — в ответе `message.chat.id`.

```bash
# 4. Быстрая проверка перед автозапуском
./venv/bin/python main.py   # в Telegram придёт «🟢 Бот запущен»
```

Создайте юнит `/etc/systemd/system/trading-bot.service`:

```ini
[Unit]
Description=Bybit Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/trading_bot
ExecStart=/opt/trading_bot/venv/bin/python main.py
Restart=always
RestartSec=10
# User running the bot. We deploy as root, so run as root (omit this line).
# For a dedicated user, create it, chown /opt/trading_bot, and set User=trading.
# User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trading-bot
systemctl status trading-bot
journalctl -u trading-bot -f        # живые логи
```

Бэкап по cron (например, ежедневно в 3:00):

```cron
0 3 * * * /opt/trading_bot/scripts/backup_state.sh >> /opt/trading_bot/logs/backup.log 2>&1
```

## Бэкап и перезапуск (вручную)

```bash
./scripts/backup_state.sh   # копия trades.db и .env в backups/
./scripts/restart.sh        # перезапуск процесса (nohup/PID-файл)
```

## Примечания

- В paper-режиме Bybit-ключи не нужны; данные берутся с публичных эндпоинтов (реальные цены mainnet).
- Токен Telegram должен опрашиваться только этим процессом (иначе конфликт `getUpdates`).
- По умолчанию `BYBIT_TESTNET=True`; перед live-торговлей проверьте ключи.
- При нехватке средств бот переходит в режим `PAUSE` и уведомляет в Telegram.
