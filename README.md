# Okbitok Arbitrage Bot

A professional-grade delta-neutral arbitrage bot for perpetual futures markets on Bybit and KuCoin. Designed for real-time monitoring, automated execution, and robust failover handling.

---

## Features

* Delta-neutral arbitrage between centralized exchanges
* Real-time price tracking with REST and WebSocket feeds
* Simulation of fills, net profit, funding, and slippage
* Automated execution with dual-side order handling
* Advanced failover system with trailing SL/TP logic
* CSV-based trade logging with full position metadata
* Telegram integration for execution and alerting
* Modular, extensible architecture for multi-exchange support

---

## Project Modules

* `main.py` — Launches the orchestrated arbitrage pipeline
* `pair_monitor.py` — Monitors live quotes, detects arbitrage conditions, filters by delta lifetime
* `profit_simulator.py` — Calculates net profit considering fees and funding; selects best opportunities
* `order_manager.py` — Handles order placement, position sizing, execution logic, timeout handling
* `failover_manager.py` — Supervises open positions post-entry, closes them under stop/take conditions
* `position_manager.py` — Stores and manages the state of all active positions
* `balance_watchdog.py` — Prevents trading if account balance is unavailable or locked
* `final_pnl_fetcher.py` — Retrieves realized PnL post-position closure (second leg)
* `pnl_fetcher.py` — Queries current unrealized PnL for monitoring and decisions
* `fill_simulator.py` — Simulates whether entry prices are realistically fillable at the moment
* `funding_fetcher.py` — Retrieves current and projected funding rates per exchange
* `signal_engine.py` — Filters and validates signals before sending them to execution logic
* `decision_engine.py` — Decides whether a signal passes all risk checks (duplicates, max positions, etc.)
* `arb_worker.py` — Background coroutine to process incoming arbitrage tasks
* `price_feed.py` — WebSocket integration and queuing for quote updates
* `symbol_specs.py` — Loads exchange-specific symbol constraints and formatting logic
* `telegram_bot.py` — Sends execution/failure/closure messages to a configured Telegram channel
* `logger.py` — Central logging configuration, supports both console and rotating file logs
* `config_manager.py` — Loads and caches values from the environment (.env)
* `advanced_trade_logger.py` — Appends and updates detailed per-trade statistics in CSV

---

## Setup Instructions

1. Clone the repository:

   ```bash
   git clone https://github.com/Alex-bitok/okbitok-arbitrage-bot.git
   cd okbitok-arbitrage-bot
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create `.env` configuration:
   See `.env.example` for required keys:

   ```
   BYBIT_KEY=
   BYBIT_SECRET=
   KUCOIN_KEY=
   KUCOIN_SECRET=
   KUCOIN_PASSPHRASE=
   ```

4. Run the bot:

   ```bash
   python main.py
   ```

---

## Notes

* The bot is modular. You can extend it with other exchanges, new signal engines, or alternative execution strategies.
* Failover logic is integrated to minimize loss on partial fills, timeouts, and execution asymmetry.
* Trade logs are stored in `logs/trade_log.csv`, which includes full PnL breakdown, durations, and entry/exit metadata.

---

## Notice
This repository contains an early demonstration version intended for architecture showcase and educational purposes only.
The production-ready version of Ok-bitok Arbitrage Bot has been significantly enhanced and is not open-sourced.

This code is not suitable for live trading and may contain known issues.

For partnership requests, pilot access, or a walkthrough of the latest system, please contact us: info@ok-bitok.ru.

---

## License

MIT — Free for commercial and non-commercial use with attribution.
