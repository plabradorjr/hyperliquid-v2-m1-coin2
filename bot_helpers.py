from strategy_config import *  # noqa: F401,F403
from hyperliquid_client import HyperliquidClient, my_print
import time
from typing import Any, Optional


def get_timeframe_in_seconds(timeframe):
    """Converts timeframe string to seconds."""
    if 'm' in timeframe:
        return int(timeframe.replace('m', '')) * 60
    elif 'h' in timeframe:
        return int(timeframe.replace('h', '')) * 3600
    elif 'd' in timeframe:
        return int(timeframe.replace('d', '')) * 86400
    else:
        raise ValueError(f"Unsupported timeframe format: {timeframe}")


def retry_api_call(api_call_lambda, max_retries=5, delay=2):
    """
    Retries an API call with a specified delay between retries.
    """
    for i in range(max_retries):
        try:
            return api_call_lambda()
        except Exception as e:
            my_print(
                f"API call failed with error: {e}. Retrying in {delay} seconds...", verbose)
            time.sleep(delay)
    raise Exception(f"API call failed after {max_retries} retries.")


def _flatten_kv(obj: Any):
    """Yield (key_path, key, value) for all dict entries recursively."""
    stack = [("", obj)]
    while stack:
        path, current = stack.pop()
        if isinstance(current, dict):
            for k, v in current.items():
                new_path = f"{path}.{k}" if path else str(k)
                yield (new_path, k, v)
                stack.append((new_path, v))
        elif isinstance(current, list):
            for i, v in enumerate(current):
                new_path = f"{path}[{i}]" if path else f"[{i}]"
                stack.append((new_path, v))


def _extract_stop_loss_price_from_info(info: dict) -> Optional[float]:
    """Extract a stop-loss trigger price from the exchange-specific info dict.

    Looks for specific SL price keys only (avoid generic substring matches).
    """
    include_keys = (
        "stoplossprice",
        "stopprice",
        "triggerprice",
        "triggerpx",
        "slprice",
        "sltriggerpx",
    )
    for _, key, value in _flatten_kv(info):
        if not isinstance(key, str):
            continue
        key_l = key.lower()
        if any(inc in key_l for inc in include_keys):
            try:
                num = float(value)
                if num > 0:
                    return num
            except Exception:
                continue
    return None


def _is_stop_loss_order(order: dict, close_side: str) -> bool:
    """Heuristic to identify a stop-loss order among open orders.

    Prioritize the order 'type' field to disambiguate TP vs SL on exchanges
    that reuse generic trigger fields.
    """
    if order.get("side", "").lower() != close_side:
        return False
    info = order.get("info", {}) or {}
    type_str = str(order.get("type") or "").lower()
    info_str = str(info).lower()

    # If the type explicitly says take profit, it's not an SL
    if "take profit" in type_str or "takeprofit" in type_str or " tp" in type_str:
        return False

    # If the type explicitly says stop, we treat it as SL
    if "stop" in type_str or " sl" in type_str:
        return True

    # Explicit TP markers present? Then not SL
    has_tp_marker = (
        (order.get("takeProfitPrice") not in (None, 0, 0.0)) or
        (_extract_take_profit_price_from_info(info) is not None) or
        ("take profit" in info_str)
    )
    if has_tp_marker:
        return False

    # Explicit SL price markers present?
    has_explicit_sl_price = (
        (order.get("stopLossPrice") not in (None, 0, 0.0)) or
        (order.get("stopPrice") not in (None, 0, 0.0)) or
        (_extract_stop_loss_price_from_info(info) is not None)
    )
    if has_explicit_sl_price:
        return True

    # Fallback: stop hint in info text
    return "stop" in info_str


def _extract_take_profit_price_from_info(info: dict) -> Optional[float]:
    """Extract a take-profit trigger price from the exchange-specific info dict.

    Restrict to explicit TP price keys only to avoid false positives like 'limitPx' or 'isPositionTpsl'.
    """
    include_keys = (
        "takeprofitprice",
        "tpprice",
        "tp_price",
        "take_profit_price",
    )
    for _, key, value in _flatten_kv(info):
        if not isinstance(key, str):
            continue
        key_l = key.lower()
        if any(inc in key_l for inc in include_keys):
            try:
                num = float(value)
                if num > 0:
                    return num
            except Exception:
                continue
    return None


def _is_take_profit_order(order: dict, close_side: str) -> bool:
    """Heuristic to identify a take-profit order among open orders."""
    if order.get("side", "").lower() != close_side:
        return False
    info = order.get("info", {}) or {}
    type_str = str(order.get("type") or "").lower()
    info_str = str(info).lower()

    # Strong hint via type field
    if "take profit" in type_str or "takeprofit" in type_str or " tp" in type_str:
        return True

    # Explicit TP price present
    if (order.get("takeProfitPrice") not in (None, 0, 0.0)) or (_extract_take_profit_price_from_info(info) is not None):
        return True

    # If info mentions take profit, accept as TP
    if "take profit" in info_str or "tp" in info_str:
        return True

    return False


def _find_current_stop_loss_order(open_orders: list, close_side: str) -> tuple[Optional[dict], Optional[float]]:
    """Return (order, stop_price) for the most relevant SL order if any."""
    candidate = None
    candidate_price = None
    for o in open_orders:
        if not _is_stop_loss_order(o, close_side):
            continue
        sl_px = _extract_stop_loss_price_from_info(o.get("info", {}) or {})
        if sl_px is None:
            try:
                v = o.get("stopPrice")
                if v is not None:
                    sl_px = float(v)
            except Exception:
                pass
        if sl_px is None:
            try:
                v = o.get("triggerPrice")
                if v is not None:
                    sl_px = float(v)
            except Exception:
                pass
        # prefer orders with a recognizable stop price
        if sl_px is None:
            continue
        if candidate is None:
            candidate, candidate_price = o, sl_px
        else:
            # For long (close_side=sell) keep the highest stop; for short keep the lowest
            if close_side == "sell":
                if sl_px > (candidate_price or -float("inf")):
                    candidate, candidate_price = o, sl_px
            else:
                if sl_px < (candidate_price or float("inf")):
                    candidate, candidate_price = o, sl_px
    return candidate, candidate_price


def _cancel_existing_stop_orders_for_side(client: HyperliquidClient, symbol: str, close_side: str) -> int:
    """Cancel all existing stop-loss orders for a given close side.

    Returns the number of canceled orders.
    """
    cancelled = 0
    try:
        open_orders = retry_api_call(lambda: client.fetch_open_orders(symbol))
        for o in open_orders:
            if _is_stop_loss_order(o, close_side) and o.get("id"):
                retry_api_call(lambda oid=o.get(
                    "id"): client.cancel_order(oid, symbol))
                cancelled += 1
        if cancelled > 0:
            my_print(
                f"Canceled {cancelled} existing SL order(s) for side {close_side}.", verbose)
    except Exception as e:
        my_print(
            f"Warning: failed to cancel existing SL orders for side {close_side}: {e}", verbose)
    return cancelled


def _ensure_single_stop_loss_order_for_side(client: HyperliquidClient, symbol: str, close_side: str) -> int:
    """Ensure at most one SL order exists; cancel extras.

    Keeps the 'tightest' stop: for long closes (sell) keep highest price;
    for short closes (buy) keep lowest price. Returns number of canceled orders.
    """
    cancelled = 0
    try:
        open_orders = retry_api_call(lambda: client.fetch_open_orders(symbol))
        candidates = []
        for o in open_orders:
            if not _is_stop_loss_order(o, close_side):
                continue
            sl_px = _extract_stop_loss_price_from_info(o.get("info", {}) or {})
            if sl_px is None:
                try:
                    v = o.get("stopPrice")
                    if v is not None:
                        sl_px = float(v)
                except Exception:
                    pass
            if sl_px is None:
                try:
                    v = o.get("triggerPrice")
                    if v is not None:
                        sl_px = float(v)
                except Exception:
                    pass
            if sl_px is not None:
                candidates.append((o, float(sl_px)))
        if len(candidates) <= 1:
            return 0
        # Choose keeper
        if close_side == "sell":
            keeper = max(candidates, key=lambda t: t[1])[0]
        else:
            keeper = min(candidates, key=lambda t: t[1])[0]
        # Cancel others
        for o, _ in candidates:
            if o is keeper:
                continue
            if o.get("id"):
                retry_api_call(lambda oid=o.get(
                    "id"): client.cancel_order(oid, symbol))
                cancelled += 1
        if cancelled > 0:
            my_print(
                f"Enforced single SL: canceled {cancelled} extra order(s) for side {close_side}.", verbose)
    except Exception as e:
        my_print(
            f"Warning: failed enforcing single SL for side {close_side}: {e}", verbose)
    return cancelled


def _cancel_existing_take_profit_orders_for_side(client: HyperliquidClient, symbol: str, close_side: str) -> int:
    """Cancel all existing take-profit orders for a given close side.

    Returns the number of canceled orders.
    """
    cancelled = 0
    try:
        open_orders = retry_api_call(lambda: client.fetch_open_orders(symbol))
        for o in open_orders:
            if _is_take_profit_order(o, close_side) and o.get("id"):
                retry_api_call(lambda oid=o.get(
                    "id"): client.cancel_order(oid, symbol))
                cancelled += 1
        if cancelled > 0:
            my_print(
                f"Canceled {cancelled} existing TP order(s) for side {close_side}.", verbose)
    except Exception as e:
        my_print(
            f"Warning: failed to cancel existing TP orders for side {close_side}: {e}", verbose)
    return cancelled


def _update_trailing_stop_if_needed(client: HyperliquidClient, symbol: str, position: dict, current_price: float, debug_orders: bool = False):
    """Maintain stop-loss for an open position based on config.

    - If trailing SL is enabled, tighten it (never loosen).
    - If trailing SL is disabled but SLs are enabled, ensure a static SL exists.
    """
    try:
        # Respect global SL ignore flag
        if ignore_sl:
            return

        side = (position.get("side") or "").lower()
        if side not in ("long", "short"):
            return

        close_side = "sell" if side == "long" else "buy"

        # Determine whether trailing is active and compute the desired level accordingly
        trailing_pct = float(params.get("trailing_sl_pct", 0))
        trailing_active = (trailing_pct > 0) and (not ignore_trailing_sl)

        if trailing_active:
            desired_sl = compute_trailing_long_sl_level(
                current_price) if side == "long" else compute_trailing_short_sl_level(current_price)
        else:
            # Fallback to static SL when trailing is disabled
            desired_sl = compute_long_sl_level(
                current_price) if side == "long" else compute_short_sl_level(current_price)
        # Only log intended SL if debug flag is enabled
        if debug_orders:
            try:
                mode = f"trailing {trailing_pct}%" if trailing_active else "static"
                my_print(
                    f"[SL] side={side} close_side={close_side} mode={mode} desired_sl={desired_sl}", verbose)
            except Exception:
                pass

        # Best-effort: ensure only one SL exists on this side each cycle
        try:
            _ensure_single_stop_loss_order_for_side(client, symbol, close_side)
        except Exception:
            pass

        # Fetch open orders and locate the current SL if any
        open_orders = retry_api_call(lambda: client.fetch_open_orders(symbol))
        if debug_orders:
            try:
                my_print(f"[DEBUG] Open orders ({len(open_orders)}):", verbose)
                for o in open_orders:
                    oid = o.get("id")
                    side_o = o.get("side")
                    t = o.get("type")
                    info = o.get("info", {}) or {}
                    sl_px = _extract_stop_loss_price_from_info(info)
                    tp_px = _extract_take_profit_price_from_info(info)
                    my_print(
                        f"  id={oid} side={side_o} type={t} SLpx={sl_px} TPpx={tp_px}", verbose)
            except Exception:
                pass
        existing_sl_order, existing_sl_price = _find_current_stop_loss_order(
            open_orders, close_side)
        if debug_orders:
            # If no SL is found, log a compact summary for debugging
            if existing_sl_price is None:
                try:
                    my_print(
                        f"[SL] No existing stop-loss detected for close_side={close_side}. OpenOrders={len(open_orders)}", verbose)
                    # Print a brief single-line summary of each order
                    for o in open_orders:
                        info = o.get("info", {}) or {}
                        sl_px = _extract_stop_loss_price_from_info(info)
                        tp_px = _extract_take_profit_price_from_info(info)
                        my_print(
                            f"  order id={o.get('id')} side={o.get('side')} type={o.get('type')} hasSL={sl_px is not None} hasTP={tp_px is not None}",
                            verbose,
                        )
                except Exception:
                    pass
            else:
                # Brief summary of the candidate we think is the SL
                try:
                    info = (existing_sl_order or {}).get("info", {}) or {}
                    sl_px = _extract_stop_loss_price_from_info(info)
                    tp_px = _extract_take_profit_price_from_info(info)
                    my_print(
                        f"[SL] Detected existing SL candidate id={(existing_sl_order or {}).get('id')} side={(existing_sl_order or {}).get('side')} type={(existing_sl_order or {}).get('type')} hasSL={sl_px is not None} hasTP={tp_px is not None}",
                        verbose,
                    )
                except Exception:
                    pass
        if debug_orders:
            my_print(
                f"[DEBUG] Existing SL: price={existing_sl_price} order_id={existing_sl_order.get('id') if existing_sl_order else None}", verbose)

        # Decide whether to place or update the stop
        should_update = False
        action_desc = ""
        if existing_sl_price is None:
            should_update = True
            action_desc = "Placed initial"
        else:
            if trailing_active:
                if side == "long":
                    should_update = desired_sl > existing_sl_price
                else:
                    should_update = desired_sl < existing_sl_price
                action_desc = "Tightened" if should_update else "Kept"
            else:
                # Static SL active: don't move an existing SL, only ensure one exists
                should_update = False
                action_desc = "Exists"

        # Log detection and decision only when debugging
        if debug_orders:
            try:
                my_print(
                    f"[SL] existing={existing_sl_price} desired={desired_sl} decision={'update' if should_update else 'skip'} reason={action_desc}",
                    verbose,
                )
            except Exception:
                pass

        if not should_update and existing_sl_price is not None:
            return

        # Cancel ALL existing SL orders on this side before placing a new one
        _cancel_existing_stop_orders_for_side(client, symbol, close_side)

        # Place a new SL for the full position size
        contracts = abs(float(position.get("contracts") or 0))
        if contracts <= 0:
            return

        mark_px = float(current_price)
        try:
            client._place_stop_loss_order(
                symbol, "buy" if side == "long" else "sell", contracts, mark_px, float(desired_sl))
            my_print(
                f"{action_desc} SL -> {desired_sl:.6f}{' (trailing ' + str(trailing_pct) + '%)' if trailing_active else ' (static)'}",
                verbose,
            )
            # Ensure we have only one SL after placement
            _ensure_single_stop_loss_order_for_side(client, symbol, close_side)
        except Exception as e:
            my_print(f"Failed to place SL: {e}", verbose)
    except Exception as e:
        my_print(f"Error in SL maintenance: {e}", verbose)
