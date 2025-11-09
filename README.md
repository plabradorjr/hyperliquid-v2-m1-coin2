# Hyperliquid Trading Bot (minimal)

This repository contains a small trading bot split into three modules:

- `hyperliquid_client.py` — CCXT-based Hyperliquid client (connects, fetches data, places orders)
- `strategy_config.py` — strategy parameters, indicator calculation and entry/exit rules
- `main.py` — the trading bot entrypoint (loads env, composes client + strategy, executes)

This README explains how to set up a local environment and run the bot.

## Prerequisites

- Python 3.10+ (3.11 recommended)
- A working `pip` installation
- A Hyperliquid-compatible wallet address and private key (for live trading)

Note: Running the bot as-is will attempt to connect to the exchange. Use testnet credentials if you don't want to trade real funds.

## Setup (recommended)

1. Create and activate a virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Provide credentials via environment variables. Create a `.env` file in the project root with the following keys:

```
HYPERLIQUID_WALLET_ADDRESS=your_wallet_address_here
HYPERLIQUID_PRIVATE_KEY=your_private_key_here
```

Warning: never commit private keys to version control. Keep `.env` in `.gitignore`.

## Run

To run the bot (single-run, not a service):

```bash
python main.py
```

`main.py` will:
- load `.env` variables
- initialize `HyperliquidClient`
- fetch balance and OHLCV data
- compute indicators and evaluate entry/exit rules
- place orders when signals occur

### Runtime flags

- `--debug-orders`: print open-order inspection and the detected/maintained stop-loss price each cycle.

Example:

```
python main.py --debug-orders
```

### Trailing Stop-Loss

You can enable a simple trailing stop-loss that is evaluated on every run of `main.py` by setting `trailing_sl_pct` under `params` in `strategy_config.py` (set to `0` to disable). The bot will:
- Place an initial stop at the trailing level on entry (if `ignore_sl` is `False`).
- On each cycle, tighten the stop if price moves favorably (never loosens it).
- When tightening, it cancels any existing stop-loss orders on that close side to avoid duplicates.
- On initial entry (when placing the first SL), it also cancels any leftover stop-loss orders from previous sessions for that symbol/close side.

Take-profit handling:
- Before placing a new take-profit on entry, it cancels any leftover take-profit orders for that symbol/close side to avoid duplicates.

Example in `strategy_config.py`:

```
params = {
  # ...
  "trailing_sl_pct": 3,  # 3% trail; set 0 to disable
}
```

If you want to avoid live order placement, test with testnet credentials and small sizing.

## Files you may edit

- `strategy_config.py` — tune `params`, indicator windows, and entry/exit rules.
- `main.py` — orchestrates trading logic; you can add logging, scheduling, or a loop.
- `hyperliquid_client.py` — client abstraction for CCXT (avoid editing unless adding features).

## Troubleshooting

- ModuleNotFoundError: make sure the virtualenv is activated and `pip install -r requirements.txt` completed.
- CCXT/hyperliquid errors: ensure your `ccxt` installation supports the `hyperliquid` exchange and that keys are correct.
- Permission errors: on macOS you may need to allow network access or run from your user account.

## Safety & recommendations

- Test with small amounts or on a testnet when possible.
- Add logging before running live.
- Handle exceptions and rate limits for production usage.

## Optional next steps I can do for you

- Create a `requirements.txt` (done) — or pin package versions
- Add a `--once` flag to run a single iteration
- Add unit tests for strategy functions

---
