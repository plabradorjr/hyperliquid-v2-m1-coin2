import ccxt  # type: ignore
import pandas as pd  # type: ignore


class HyperliquidClient:
    """Simple synchronous client for Hyperliquid exchange using CCXT."""

    def __init__(self, wallet_address: str, private_key: str):
        """Initialize the Hyperliquid client.

        Args:
            wallet_address: Your Hyperliquid wallet address
            private_key: Your wallet's private key
        """
        if not wallet_address:
            raise ValueError("wallet_address is required")

        if not private_key:
            raise ValueError("private_key is required")

        try:
            self.exchange = ccxt.hyperliquid({
                "walletAddress": wallet_address,
                "privateKey": private_key,
                "enableRateLimit": True,
            })
            self.markets = {}
            self._load_markets()
        except Exception as e:
            raise Exception(f"Failed to initialize exchange: {str(e)}")

    def _load_markets(self) -> None:
        """Load market data from the exchange."""
        try:
            self.markets = self.exchange.load_markets()
        except Exception as e:
            raise Exception(f"Failed to load markets: {str(e)}")

    def _amount_to_precision(self, symbol: str, amount: float) -> float:
        """Convert amount to exchange precision requirements.

        Args:
            symbol: Trading pair symbol
            amount: Order amount to format

        Returns:
            Amount formatted with correct precision as float
        """
        try:
            result = self.exchange.amount_to_precision(symbol, amount)
            return float(result)
        except Exception as e:
            raise Exception(f"Failed to format amount precision: {str(e)}")

    def _price_to_precision(self, symbol: str, price: float) -> float:
        """Convert price to exchange precision requirements.

        Args:
            symbol: Trading pair symbol
            price: Order price to format

        Returns:
            Price formatted with correct precision as float
        """
        try:
            result = self.exchange.price_to_precision(symbol, price)
            return float(result)
        except Exception as e:
            raise Exception(f"Failed to format price precision: {str(e)}")

    def get_current_price(self, symbol: str) -> float:
        """Get the current market price for a symbol.

        Args:
            symbol: Trading pair (e.g., "ETH/USDC:USDC")

        Returns:
            Current market price
        """
        try:
            return float(self.markets[symbol]["info"]["midPx"])
        except Exception as e:
            raise Exception(f"Failed to get price for {symbol}: {str(e)}")

    def fetch_balance(self) -> dict:
        """Fetch account balance information.

        Returns:
            Account balance data
        """
        try:
            result = self.exchange.fetch_balance()
            return result
        except Exception as e:
            raise Exception(f"Failed to fetch balance: {str(e)}")

    def fetch_positions(self, symbols: list[str]) -> list:
        """Fetch open positions for specified symbols.

        Args:
            symbols: List of trading pairs

        Returns:
            List of position dictionaries with active positions
        """
        try:
            positions = self.exchange.fetch_positions(symbols)
            return [pos for pos in positions if float(pos["contracts"]) != 0]
        except Exception as e:
            raise Exception(f"Failed to fetch positions: {str(e)}")

    def fetch_open_orders(self, symbol: str) -> list:
        """Fetch open orders for a symbol."""
        try:
            return self.exchange.fetch_open_orders(symbol)
        except Exception as e:
            raise Exception(f"Failed to fetch open orders: {str(e)}")

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an open order by ID."""
        try:
            return self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            raise Exception(f"Failed to cancel order {order_id}: {str(e)}")

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV candlestick data.

        Args:
            symbol: Trading pair symbol
            timeframe: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d)
            limit: Maximum number of candles to fetch

        Returns:
            DataFrame with OHLCV data
        """
        try:
            ohlcv_data = self.exchange.fetch_ohlcv(
                symbol, timeframe, limit=limit)

            df = pd.DataFrame(
                data=ohlcv_data,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp").sort_index()

            numeric_cols = ["open", "high", "low", "close", "volume"]
            df[numeric_cols] = df[numeric_cols].astype(float)

            return df
        except Exception as e:
            raise Exception(f"Failed to fetch OHLCV data: {str(e)}")

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol.

        Args:
            symbol: Trading pair symbol
            leverage: Leverage multiplier

        Returns:
            True if successful
        """
        try:
            self.exchange.set_leverage(leverage, symbol)
            return True
        except Exception as e:
            raise Exception(f"Failed to set leverage: {str(e)}")

    def set_margin_mode(self, symbol: str, margin_mode: str, leverage: int) -> bool:
        """Set margin mode for a symbol.

        Args:
            symbol: Trading pair symbol
            margin_mode: "isolated" or "cross"
            leverage: Required leverage multiplier for Hyperliquid

        Returns:
            True if successful
        """
        try:
            self.exchange.set_margin_mode(
                margin_mode, symbol, params={"leverage": leverage})
            return True
        except Exception as e:
            raise Exception(f"Failed to set margin mode: {str(e)}")

    def place_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reduce_only: bool = False,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None,
        tp_size_pct: float | None = None,
    ) -> dict:
        """Place a market order with optional take profit and stop loss.

        Args:
            symbol: Trading pair symbol
            side: "buy" or "sell"
            amount: Order size in contracts
            reduce_only: If True, order will only reduce position size
            take_profit_price: Optional price level to take profit
            stop_loss_price: Optional price level to stop loss
            tp_size_pct: Optional percentage of the entry amount to use for the
                take-profit order size (e.g., 50 = 50%, defaults to 100 if None)

        Returns:
            Order execution details
        """
        try:
            formatted_amount = self._amount_to_precision(symbol, amount)

            price = float(self.markets[symbol]["info"]["midPx"])
            formatted_price = self._price_to_precision(symbol, price)

            params = {"reduceOnly": reduce_only}

            if take_profit_price is not None:
                formatted_tp_price = self._price_to_precision(
                    symbol, take_profit_price)
                params["takeProfitPrice"] = formatted_tp_price

            # Do NOT attach stopLossPrice to the entry order. We manage SL
            # as a separate reduce-only order to avoid duplicate stop orders
            # and to make trailing updates simpler and deterministic.

            order_info = {}
            order_info_final = {}

            order_info["market_order"] = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=formatted_amount,
                price=formatted_price,
                params=params
            )
            order_info_final["market_order"] = order_info["market_order"]["info"]

            if take_profit_price is not None:
                # Determine TP order size as percentage of entry amount
                tp_pct = 100.0 if tp_size_pct is None else float(tp_size_pct)
                tp_amount_raw = max(0.0, amount * (tp_pct / 100.0))
                tp_formatted_amount = self._amount_to_precision(
                    symbol, tp_amount_raw)

                if tp_formatted_amount > 0:
                    order = self._place_take_profit_order(
                        symbol, side, tp_formatted_amount, formatted_price, take_profit_price)
                    order_info_final["take_profit_order"] = order["info"]

            if stop_loss_price is not None:
                order = self._place_stop_loss_order(
                    symbol, side, formatted_amount, formatted_price, stop_loss_price)
                order_info_final["stop_loss_order"] = order["info"]

            return order_info_final
        except Exception as e:
            raise Exception(f"Failed to place market order: {str(e)}")

    def _place_take_profit_order(self, symbol: str, side: str, amount: float, price: float, take_profit_price: float) -> dict:
        """Internal method to place a take-profit order."""
        tp_price = self._price_to_precision(symbol, take_profit_price)
        amount = self._amount_to_precision(symbol, amount)
        close_side = "sell" if side == "buy" else "buy"
        return self.exchange.create_order(
            symbol=symbol,
            type="market",
            side=close_side,
            amount=amount,
            price=price,
            params={"takeProfitPrice": tp_price, "reduceOnly": True},
        )

    def _place_stop_loss_order(self, symbol: str, side: str, amount: float, price: float, stop_loss_price: float) -> dict:
        """Internal method to place a stop-loss order."""
        sl_price = self._price_to_precision(symbol, stop_loss_price)
        amount = self._amount_to_precision(symbol, amount)
        close_side = "sell" if side == "buy" else "buy"
        return self.exchange.create_order(
            symbol=symbol,
            type="market",
            side=close_side,
            amount=amount,
            price=price,
            params={"stopLossPrice": sl_price, "reduceOnly": True},
        )


def my_print(message: str, verbose: bool):
    if verbose:
        print(message)
