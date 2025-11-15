from strategy_config import *
from hyperliquid_client import HyperliquidClient, my_print
from bot_helpers import (
    get_timeframe_in_seconds,
    retry_api_call,
    _update_trailing_stop_if_needed,
    _cancel_existing_stop_orders_for_side,
    _cancel_existing_take_profit_orders_for_side,
    _ensure_single_stop_loss_order_for_side,
)
import os
import argparse
from dotenv import load_dotenv  # type: ignore
import time
from typing import Any, Optional

load_dotenv()

DEBUG_ORDERS = False


def run_strategy():
    try:
        # ==========================================
        # 1. Initialize Client
        # ==========================================
        wallet_address = os.getenv("HYPERLIQUID_WALLET_ADDRESS")
        private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
        client = HyperliquidClient(wallet_address, private_key)

        # ==========================================
        # 2. Get Account Information
        # ==========================================
        balance_info = retry_api_call(lambda: client.fetch_balance())
        balance = float(balance_info["total"]["USDC"])
        my_print(f"Current balance: ${balance}", verbose)

        # ==========================================
        # 3. Get Market Data
        # ==========================================
        # Fetch OHLCV data
        df = retry_api_call(lambda: client.fetch_ohlcv(
            params["symbol"], params["timeframe"]))
        # Compute indicators
        df = compute_indicators(df)

        # Get current and previous candle
        current_candle = df.iloc[-2]
        previous_candle = df.iloc[-3]
        current_price = current_candle['close']

        # ==========================================
        # 4. Check Positions & Execute Strategy
        # ==========================================
        # Check for open positions
        positions = retry_api_call(
            lambda: client.fetch_positions([params["symbol"]]))
        current_position = positions[0] if positions else None

        # Print current position info
        if current_position:
            position_info = (
                f"current position: {current_position['info']['position']['coin']} - {current_position['side']}\n"
                f"unrealizedPnl: ${current_position['unrealizedPnl']}\n"
                f"positionValue: ${current_position['info']['position']['positionValue']}\n"
            )
            my_print(position_info, verbose)
        else:
            my_print("Current position: None", verbose)

        if current_position:
            # ----------------------------------------
            # 4a. Position Management
            # ----------------------------------------
            position_side = current_position["side"].lower()

            # Evaluate trailing stop-loss on every run if configured
            _update_trailing_stop_if_needed(
                client, params["symbol"], current_position, current_price, DEBUG_ORDERS)

            # Check long exit
            if position_side == "long" and not ignore_longs and not ignore_exit:
                if check_long_exit_condition(current_candle, previous_candle):
                    my_print("Long exit signal detected", verbose)
                    retry_api_call(lambda: client.place_market_order(
                        params["symbol"],
                        "sell",
                        abs(current_position["contracts"]),
                        reduce_only=True
                    ))
                    my_print("Long position closed", verbose)
                    # Immediately check short entry after closing the long position
                    try:
                        # Ensure leverage and margin mode are set before opening a new position
                        retry_api_call(lambda: client.set_leverage(
                            symbol=params["symbol"],
                            leverage=params["leverage"]
                        ))

                        retry_api_call(lambda: client.set_margin_mode(
                            symbol=params["symbol"],
                            margin_mode=params["margin_mode"],
                            leverage=params["leverage"]
                        ))

                        # Check and open short entry immediately
                        if not ignore_shorts and check_short_entry_condition(current_candle, previous_candle):
                            my_print("Short entry signal detected", verbose)

                            # Calculate position size
                            position_size = calculate_position_size(balance)
                            amount = position_size / current_price

                            # Calculate TP/SL levels only if not ignored
                            tp_price = None
                            sl_price = None

                            if not ignore_tp:
                                tp_price = compute_short_tp_level(
                                    current_price)

                            if not ignore_sl:
                                trailing = float(params.get(
                                    "trailing_sl_pct", 0)) if not ignore_trailing_sl else 0
                                if trailing > 0:
                                    sl_price = compute_trailing_short_sl_level(
                                        current_price)
                                else:
                                    sl_price = compute_short_sl_level(
                                        current_price)

                            my_print(
                                f"Opening short position with TP at {tp_price} and SL at {sl_price}", verbose)

                            # Clear any leftover SL orders from previous session before placing new SL
                            if sl_price is not None:
                                _cancel_existing_stop_orders_for_side(
                                    client, params["symbol"], "buy")

                            # Clear any leftover TP orders from previous session before placing new TP
                            if tp_price is not None:
                                _cancel_existing_take_profit_orders_for_side(
                                    client, params["symbol"], "buy")

                            # Open position with optional TP/SL
                            orders = retry_api_call(lambda: client.place_market_order(
                                params["symbol"],
                                "sell",
                                amount,
                                take_profit_price=tp_price,
                                stop_loss_price=sl_price,
                                tp_size_pct=params.get("tp_size_pct")
                            ))

                            if orders.get("market_order"):
                                my_print(
                                    f"Short position opened: {orders['market_order']['resting']}", verbose)

                                if orders.get("take_profit_order"):
                                    my_print(
                                        f"Short take profit order placed: {orders['take_profit_order']['resting']}", verbose)

                                if orders.get("stop_loss_order"):
                                    my_print(
                                        f"Short stop loss order placed: {orders['stop_loss_order']['resting']}", verbose)
                                    _ensure_single_stop_loss_order_for_side(
                                        client, params["symbol"], "buy")
                    except Exception as e:
                        my_print(
                            f"Error during immediate short entry after long exit: {e}", verbose)

            # Check short exit
            elif position_side == "short" and not ignore_shorts and not ignore_exit:
                if check_short_exit_condition(current_candle, previous_candle):
                    my_print("Short exit signal detected", verbose)
                    retry_api_call(lambda: client.place_market_order(
                        params["symbol"],
                        "buy",
                        abs(current_position["contracts"]),
                        reduce_only=True
                    ))
                    my_print("Short position closed", verbose)
                    # Immediately check long entry after closing the short position
                    try:
                        # Ensure leverage and margin mode are set before opening a new position
                        retry_api_call(lambda: client.set_leverage(
                            symbol=params["symbol"],
                            leverage=params["leverage"]
                        ))

                        retry_api_call(lambda: client.set_margin_mode(
                            symbol=params["symbol"],
                            margin_mode=params["margin_mode"],
                            leverage=params["leverage"]
                        ))

                        # Check and open long entry immediately
                        if not ignore_longs and check_long_entry_condition(current_candle, previous_candle):
                            my_print("Long entry signal detected", verbose)

                            # Calculate position size
                            position_size = calculate_position_size(balance)
                            amount = position_size / current_price

                            # Calculate TP/SL levels only if not ignored
                            tp_price = None
                            sl_price = None

                            if not ignore_tp:
                                tp_price = compute_long_tp_level(current_price)

                            if not ignore_sl:
                                trailing = float(params.get(
                                    "trailing_sl_pct", 0)) if not ignore_trailing_sl else 0
                                if trailing > 0:
                                    sl_price = compute_trailing_long_sl_level(
                                        current_price)
                                else:
                                    sl_price = compute_long_sl_level(
                                        current_price)

                            my_print(
                                f"Opening long position with TP at {tp_price} and SL at {sl_price}", verbose)

                            # Clear any leftover SL orders from previous session before placing new SL
                            if sl_price is not None:
                                _cancel_existing_stop_orders_for_side(
                                    client, params["symbol"], "sell")

                            # Open position with optional TP/SL
                            orders = retry_api_call(lambda: client.place_market_order(
                                params["symbol"],
                                "buy",
                                amount,
                                take_profit_price=tp_price,
                                stop_loss_price=sl_price,
                                tp_size_pct=params.get("tp_size_pct")
                            ))

                            if orders.get("market_order"):
                                my_print(
                                    f"Long position opened: {orders['market_order']['resting']}", verbose)

                                if orders.get("take_profit_order"):
                                    my_print(
                                        f"Long take profit order placed: {orders['take_profit_order']['resting']}", verbose)

                                if orders.get("stop_loss_order"):
                                    my_print(
                                        f"Long stop loss order placed: {orders['stop_loss_order']['resting']}", verbose)
                                    _ensure_single_stop_loss_order_for_side(
                                        client, params["symbol"], "sell")
                    except Exception as e:
                        my_print(
                            f"Error during immediate long entry after short exit: {e}", verbose)

        else:
            # ----------------------------------------
            # 4b. Setup Trading Account
            # ----------------------------------------
            # Set leverage and margin mode before opening new positions
            retry_api_call(lambda: client.set_leverage(
                symbol=params["symbol"],
                leverage=params["leverage"]
            ))

            retry_api_call(lambda: client.set_margin_mode(
                symbol=params["symbol"],
                margin_mode=params["margin_mode"],
                leverage=params["leverage"]
            ))

            # ----------------------------------------
            # 4c. Entry Management
            # ----------------------------------------
            # Check long entry
            if not ignore_longs and check_long_entry_condition(current_candle, previous_candle):
                my_print("Long entry signal detected", verbose)

                # Calculate position size
                position_size = calculate_position_size(balance)
                amount = position_size / current_price

                # Calculate TP/SL levels only if not ignored
                tp_price = None
                sl_price = None

                if not ignore_tp:
                    tp_price = compute_long_tp_level(current_price)

                if not ignore_sl:
                    trailing = float(params.get("trailing_sl_pct", 0)
                                     ) if not ignore_trailing_sl else 0
                    if trailing > 0:
                        sl_price = compute_trailing_long_sl_level(
                            current_price)
                    else:
                        sl_price = compute_long_sl_level(current_price)

                my_print(
                    f"Opening long position with TP at {tp_price} and SL at {sl_price}", verbose)

                # Clear any leftover SL orders from previous session before placing new SL
                if sl_price is not None:
                    _cancel_existing_stop_orders_for_side(
                        client, params["symbol"], "sell")

                # Clear any leftover TP orders from previous session before placing new TP
                if tp_price is not None:
                    _cancel_existing_take_profit_orders_for_side(
                        client, params["symbol"], "sell")

                # Open position with optional TP/SL
                orders = retry_api_call(lambda: client.place_market_order(
                    params["symbol"],
                    "buy",
                    amount,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price,
                    tp_size_pct=params.get("tp_size_pct")
                ))

                if orders.get("market_order"):
                    my_print(
                        f"Long position opened: {orders['market_order']['resting']}", verbose)

                    if orders.get("take_profit_order"):
                        my_print(
                            f"Long take profit order placed: {orders['take_profit_order']['resting']}", verbose)

                    if orders.get("stop_loss_order"):
                        my_print(
                            f"Long stop loss order placed: {orders['stop_loss_order']['resting']}", verbose)
                        # Keep only one SL on the close side
                        _ensure_single_stop_loss_order_for_side(
                            client, params["symbol"], "sell")

            # Check short entry

            elif not ignore_shorts and check_short_entry_condition(current_candle, previous_candle):
                my_print("Short entry signal detected", verbose)

                # Calculate position size
                position_size = calculate_position_size(balance)
                amount = position_size / current_price

                # Calculate TP/SL levels only if not ignored
                tp_price = None
                sl_price = None

                if not ignore_tp:
                    tp_price = compute_short_tp_level(current_price)

                if not ignore_sl:
                    trailing = float(params.get("trailing_sl_pct", 0)
                                     ) if not ignore_trailing_sl else 0
                    if trailing > 0:
                        sl_price = compute_trailing_short_sl_level(
                            current_price)
                    else:
                        sl_price = compute_short_sl_level(current_price)

                my_print(
                    f"Opening short position with TP at {tp_price} and SL at {sl_price}", verbose)

                # Clear any leftover SL orders from previous session before placing new SL
                if sl_price is not None:
                    _cancel_existing_stop_orders_for_side(
                        client, params["symbol"], "buy")

                # Clear any leftover TP orders from previous session before placing new TP
                if tp_price is not None:
                    _cancel_existing_take_profit_orders_for_side(
                        client, params["symbol"], "buy")

                # Open position with optional TP/SL
                orders = retry_api_call(lambda: client.place_market_order(
                    params["symbol"],
                    "sell",
                    amount,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price,
                    tp_size_pct=params.get("tp_size_pct")
                ))

                if orders.get("market_order"):
                    my_print(
                        f"Short position opened: {orders['market_order']['resting']}", verbose)

                    if orders.get("take_profit_order"):
                        my_print(
                            f"Short take profit order placed: {orders['take_profit_order']['resting']}", verbose)

                    if orders.get("stop_loss_order"):
                        my_print(
                            f"Short stop loss order placed: {orders['stop_loss_order']['resting']}", verbose)
                        # Keep only one SL on the close side
                        _ensure_single_stop_loss_order_for_side(
                            client, params["symbol"], "buy")

    except Exception as e:
        my_print(f"Error in main loop: {e}", verbose)


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Hyperliquid bot")
        parser.add_argument("--debug-orders", action="store_true",
                            help="log open orders and SL detection")
        args = parser.parse_args()
        globals()["DEBUG_ORDERS"] = bool(args.debug_orders)

        if DEBUG_ORDERS:
            my_print("[DEBUG] Order inspection enabled.", True)

        run_strategy()

        timeframe_seconds = get_timeframe_in_seconds(params["timeframe"])

        while True:
            current_timestamp = time.time()
            time_to_next_candle = timeframe_seconds - \
                (current_timestamp % timeframe_seconds)
            my_print(
                f"Waiting for {(time_to_next_candle / 60):.2f} minutes until next candle...", verbose)
            my_print("--------------------------------------------------", verbose)
            time.sleep(time_to_next_candle)
            run_strategy()
    except KeyboardInterrupt:
        my_print("\nGoodbye!", verbose)
