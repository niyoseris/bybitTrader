import os
import json
import time
import numpy as np
import pandas as pd
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from threading import Lock
from pybit.unified_trading import HTTP
from ta.trend import SMAIndicator, MACD
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from dotenv import load_dotenv
import logging
import math
import config
from data_collector import fetch_klines

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("btcusdt_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, api_key, api_secret, symbol="BTCUSDT", config_path='config.json', testnet=True):
        self.client = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet
        )
        self.console = Console()
        self.market_data_lock = Lock()
        self.load_config(config_path)
        self.symbol = symbol
        self.base_asset = symbol.replace("USDT", "")  # Extract base asset (e.g., BTC from BTCUSDT)
        self.positions = {}
        self.last_values = {}
        self.current_position = None  # None: no position, "long": long position
        self.entry_price = None
        self.stop_loss = None
        self.take_profit = None
        self.take_profit_levels = []  # Multiple take profit levels [(price, percentage), ...]  
        self.take_profit_triggered = []  # Track which take profit levels have been triggered
        self.last_signal = None
        self.prev_k = None
        self.prev_d = None
        self.prev_rsi = None
        self.prev_close = None
        self.sl_buffer = 75  # 75 USD buffer for stop loss (between 50-100 USD)
        
    def load_config(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                config_json = json.load(f)
                
            # We're using specific indicators for this strategy
            self.active_indicators = {
                'RSI': True,
                'STOCHASTIC': True,
                'BBANDS': True
            }
            
            self.indicator_params = config_json['indicators']
            self.trade_amount = config_json['trading']['amount']
            self.update_interval = config_json['trading']['update_interval']
            self.kline_interval = str(config_json['trading']['kline']['interval'])
            self.kline_limit = int(config_json['trading']['kline']['limit'])
            
            # Load take profit settings if they exist
            if 'take_profit' in config_json['trading']:
                self.tp_config = config_json['trading']['take_profit']
            else:
                # Default take profit configuration based on config.json take_profit_percentage
                take_profit_pct = config_json['trading'].get('take_profit_percentage', 0.5)
                self.tp_config = {
                    'use_percentage': True,  # Use percentage-based take profit
                    'percentage_levels': [take_profit_pct],  # Take profit at specified percentage
                    'quantity_per_level': [1.0]  # Sell 100% of position at the level
                }
            
            # Kline interval validation
            valid_intervals = ['1', '3', '5', '15', '30', '60', '120', '240', '360', '720', '1440']
            if self.kline_interval not in valid_intervals:
                raise ValueError(f"Invalid kline interval. Valid values: {', '.join(valid_intervals)}")
            
            self.console.print(f"[green]Configuration loaded successfully from {config_path}[/green]")
            self.console.print(f"[blue]Using {self.kline_interval} minute candles, fetching last {self.kline_limit} candles[/blue]")
            
        except Exception as e:
            self.console.print(f"[bold red]Error loading config: {str(e)}[/bold red]")
            raise
    
    def get_wallet_balance(self):
        """Get wallet balance"""
        try:
            balances = self.client.get_wallet_balance(accountType="UNIFIED")
            wallet = {}
            
            if balances.get("retCode") != 0:
                self.console.print(f"[bold red]API Error: {balances.get('retMsg')}[/bold red]")
                return {}
            
            if not balances.get("result") or not balances["result"].get("list"):
                self.console.print("[yellow]No balance data received from API[/yellow]")
                return {}
                
            for account in balances["result"]["list"]:
                if "coin" not in account:
                    continue
                    
                for coin in account["coin"]:
                    try:
                        wallet_balance = float(coin.get("walletBalance", 0))
                        if wallet_balance > 0:
                            wallet[coin["coin"]] = {
                                "free": wallet_balance - float(coin.get("locked", 0)),
                                "locked": float(coin.get("locked", 0)),
                                "total": wallet_balance
                            }
                    except (KeyError, ValueError) as e:
                        continue
            
            return wallet
            
        except Exception as e:
            self.console.print(f"[bold red]Error getting wallet balance: {str(e)}[/bold red]")
            return {}
    
    def get_min_order_size(self, symbol):
        """Get minimum order size for a symbol"""
        try:
            # Get instrument info
            instrument_info = self.client.get_instruments_info(
                category="spot",
                symbol=symbol
            )
            
            # Extract values from response
            lot_size_filter = instrument_info["result"]["list"][0]["lotSizeFilter"]
            min_qty = float(lot_size_filter["minOrderQty"])
            min_order_amt = float(lot_size_filter["minOrderAmt"])
            
            # Calculate decimal places from basePrecision
            base_precision = lot_size_filter["basePrecision"]
            decimal_places = len(base_precision.split(".")[1]) if "." in base_precision else 0
            
            return min_qty, min_order_amt, decimal_places
            
        except Exception as e:
            self.console.print(f"[bold red]Error getting min order size for {symbol}: {str(e)}[/bold red]")
            return None, None, None
    
    def get_klines(self):
        """Get historical klines/candlestick data for the trading pair"""
        try:
            klines = self.client.get_kline(
                category="spot",
                symbol=self.symbol,
                interval=self.kline_interval,
                limit=self.kline_limit
            )
            
            df = pd.DataFrame(klines["result"]["list"], 
                             columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
            
            # Convert timestamp to datetime for easier analysis
            # Explicitly convert to numeric type first to avoid FutureWarning
            df['timestamp'] = pd.to_datetime(df['timestamp'].astype(float), unit='ms')
            
            # Sort by timestamp (newest data last)
            df = df.sort_values('timestamp')
            
            return df
        except Exception as e:
            self.console.print(f"[bold red]Error fetching klines: {str(e)}[/bold red]")
            return None
    
    def calculate_indicators(self, df):
        """Calculate technical indicators for the trading pair"""
        try:
            # Calculate RSI with RMA (Wilder's) smoothing
            # For RSI, we use RMA (Wilder's) smoothing as per user preference
            close_prices = df['close'].values
            delta = np.zeros_like(close_prices)
            delta[1:] = close_prices[1:] - close_prices[:-1]
            
            # Separate gains and losses
            gains = delta.copy()
            losses = delta.copy()
            gains[gains < 0] = 0
            losses[losses > 0] = 0
            losses = abs(losses)
            
            # Calculate RMA for gains and losses (Wilder's smoothing)
            length = 14  # Standard RSI period
            avg_gains = np.zeros_like(gains)
            avg_losses = np.zeros_like(losses)
            
            # First average is simple average
            avg_gains[length] = np.mean(gains[1:length+1])
            avg_losses[length] = np.mean(losses[1:length+1])
            
            # Use RMA for subsequent values
            for i in range(length+1, len(gains)):
                avg_gains[i] = (avg_gains[i-1] * (length-1) + gains[i]) / length
                avg_losses[i] = (avg_losses[i-1] * (length-1) + losses[i]) / length
            
            # Calculate RS and RSI
            rs = np.zeros_like(close_prices)
            rsi = np.zeros_like(close_prices)
            
            for i in range(length, len(close_prices)):
                if avg_losses[i] == 0:
                    rsi[i] = 100
                else:
                    rs[i] = avg_gains[i] / avg_losses[i]
                    rsi[i] = 100 - (100 / (1 + rs[i]))
            
            df['rsi'] = rsi
            
            # Calculate Bollinger Bands
            bb = BollingerBands(df['close'], window=20, window_dev=4)
            df['bb_upper'] = bb.bollinger_hband()
            df['bb_middle'] = bb.bollinger_mavg()
            df['bb_lower'] = bb.bollinger_lband()
            
            # Calculate Stochastic Oscillator
            stoch = StochasticOscillator(df['high'], df['low'], df['close'], window=14, smooth_window=3)
            df['stoch_k'] = stoch.stoch()
            df['stoch_d'] = stoch.stoch_signal()
            
            # Store previous values for crossover detection
            if len(df) > 1:
                self.prev_k = df['stoch_k'].iloc[-2]
                self.prev_d = df['stoch_d'].iloc[-2]
                self.prev_rsi = df['rsi'].iloc[-2]
                self.prev_close = df['close'].iloc[-2]
            
            return df
        except Exception as e:
            self.console.print(f"[bold red]Error calculating indicators: {str(e)}[/bold red]")
            return None
    
    def is_stoch_k_crossing_d_up(self, df):
        """Check if Stochastic %K is crossing %D upward"""
        if self.prev_k is None or self.prev_d is None:
            return False
        
        current_k = df['stoch_k'].iloc[-1]
        current_d = df['stoch_d'].iloc[-1]
        
        # Check if K was below D and is now above D
        return (self.prev_k < self.prev_d) and (current_k > current_d)
    
    def is_stoch_k_crossing_d_down(self, df):
        """Check if Stochastic %K is crossing %D downward"""
        if self.prev_k is None or self.prev_d is None:
            return False
        
        current_k = df['stoch_k'].iloc[-1]
        current_d = df['stoch_d'].iloc[-1]
        
        # Check if K was above D and is now below D
        return (self.prev_k > self.prev_d) and (current_k < current_d)
    
    def is_rsi_rising(self, df):
        """Check if RSI is rising"""
        if self.prev_rsi is None:
            return False
        
        current_rsi = df['rsi'].iloc[-1]
        return current_rsi > self.prev_rsi
    
    def is_price_touching_bb_lower(self, df):
        """Check if price is touching or very close to lower Bollinger Band"""
        current_close = df['close'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        
        # Price is touching or within 0.2% of lower band
        return current_close <= bb_lower * 1.002
    
    def is_price_touching_bb_upper(self, df):
        """Check if price is touching upper Bollinger Band and pulling back"""
        if self.prev_close is None:
            return False
        
        current_close = df['close'].iloc[-1]
        bb_upper = df['bb_upper'].iloc[-1]
        
        # Price touched upper band and is now pulling back
        return (self.prev_close >= bb_upper * 0.998) and (current_close < self.prev_close)
    
    def is_price_breaking_bb_middle_up(self, df):
        """Check if price is breaking middle Bollinger Band upward"""
        if self.prev_close is None:
            return False
        
        current_close = df['close'].iloc[-1]
        bb_middle = df['bb_middle'].iloc[-1]
        
        # Price was below middle band and is now above
        return (self.prev_close < bb_middle) and (current_close > bb_middle)
    
    def is_price_breaking_bb_middle_down(self, df):
        """Check if price is breaking middle Bollinger Band downward"""
        if self.prev_close is None:
            return False
        
        current_close = df['close'].iloc[-1]
        bb_middle = df['bb_middle'].iloc[-1]
        
        # Price was above middle band and is now below
        return (self.prev_close > bb_middle) and (current_close < bb_middle)
    
    def check_oversold_buy_signal(self, df):
        """Check for oversold buy signal (Temel Alım Sinyali)"""
        # Get current values
        current_close = df['close'].iloc[-1]
        current_rsi = df['rsi'].iloc[-1]
        current_k = df['stoch_k'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        bb_middle = df['bb_middle'].iloc[-1]
        
        # Check conditions
        price_at_lower_band = self.is_price_touching_bb_lower(df)
        stoch_oversold_crossover = (current_k < 20) and self.is_stoch_k_crossing_d_up(df)
        rsi_condition = current_rsi < 40
        
        if price_at_lower_band and stoch_oversold_crossover and rsi_condition:
            # Calculate take profit and stop loss
            take_profit = bb_middle
            stop_loss = bb_lower - self.sl_buffer
            
            self.console.print(f"[bold green]OVERSOLD BUY SIGNAL DETECTED![/bold green]")
            self.console.print(f"Price: ${current_close:.2f}, RSI: {current_rsi:.2f}, Stoch %K: {current_k:.2f}")
            self.console.print(f"Target: ${take_profit:.2f}, Stop Loss: ${stop_loss:.2f}")
            
            return True, take_profit, stop_loss
        
        return False, None, None
    
    def check_trend_buy_signal(self, df):
        """Check for trend confirmed buy signal (Trend Onaylı Alım Sinyali)"""
        # Get current values
        current_close = df['close'].iloc[-1]
        current_rsi = df['rsi'].iloc[-1]
        current_k = df['stoch_k'].iloc[-1]
        bb_middle = df['bb_middle'].iloc[-1]
        bb_upper = df['bb_upper'].iloc[-1]
        
        # Check conditions
        breaking_middle_band = self.is_price_breaking_bb_middle_up(df)
        stoch_crossover = self.is_stoch_k_crossing_d_up(df) and (current_k > 40)
        rsi_condition = (current_rsi > 45) and self.is_rsi_rising(df)
        
        if breaking_middle_band and stoch_crossover and rsi_condition:
            # Calculate take profit and stop loss
            take_profit = bb_upper
            stop_loss = bb_middle - self.sl_buffer
            
            self.console.print(f"[bold green]TREND CONFIRMED BUY SIGNAL DETECTED![/bold green]")
            self.console.print(f"Price: ${current_close:.2f}, RSI: {current_rsi:.2f}, Stoch %K: {current_k:.2f}")
            self.console.print(f"Target: ${take_profit:.2f}, Stop Loss: ${stop_loss:.2f}")
            
            return True, take_profit, stop_loss
        
        return False, None, None
    
    def check_overbought_sell_signal(self, df):
        """Check for overbought sell signal (Temel Satım Sinyali)"""
        # Get current values
        current_close = df['close'].iloc[-1]
        current_rsi = df['rsi'].iloc[-1]
        current_k = df['stoch_k'].iloc[-1]
        bb_upper = df['bb_upper'].iloc[-1]
        bb_middle = df['bb_middle'].iloc[-1]
        
        # Check conditions
        price_at_upper_band = self.is_price_touching_bb_upper(df)
        stoch_overbought_crossover = (current_k > 80) and self.is_stoch_k_crossing_d_down(df)
        rsi_condition = current_rsi > 60
        
        if price_at_upper_band and stoch_overbought_crossover and rsi_condition:
            # Calculate take profit and stop loss
            take_profit = bb_middle
            stop_loss = bb_upper + self.sl_buffer
            
            self.console.print(f"[bold red]OVERBOUGHT SELL SIGNAL DETECTED![/bold red]")
            self.console.print(f"Price: ${current_close:.2f}, RSI: {current_rsi:.2f}, Stoch %K: {current_k:.2f}")
            self.console.print(f"Target: ${take_profit:.2f}, Stop Loss: ${stop_loss:.2f}")
            
            return True, take_profit, stop_loss
        
        return False, None, None
    
    def check_trend_sell_signal(self, df):
        """Check for trend confirmed sell signal (Trend Onaylı Satım Sinyali)"""
        # Get current values
        current_close = df['close'].iloc[-1]
        current_rsi = df['rsi'].iloc[-1]
        current_k = df['stoch_k'].iloc[-1]
        bb_middle = df['bb_middle'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        
        # Check conditions
        breaking_middle_band = self.is_price_breaking_bb_middle_down(df)
        stoch_crossover = self.is_stoch_k_crossing_d_down(df) and (current_k < 60)
        rsi_condition = current_rsi < 50
        
        if breaking_middle_band and stoch_crossover and rsi_condition:
            # Calculate take profit and stop loss
            take_profit = bb_lower
            stop_loss = bb_middle + self.sl_buffer
            
            self.console.print(f"[bold red]TREND CONFIRMED SELL SIGNAL DETECTED![/bold red]")
            self.console.print(f"Price: ${current_close:.2f}, RSI: {current_rsi:.2f}, Stoch %K: {current_k:.2f}")
            self.console.print(f"Target: ${take_profit:.2f}, Stop Loss: ${stop_loss:.2f}")
            
            return True, take_profit, stop_loss
        
        return False, None, None
    
    def setup_take_profit_levels(self, entry_price):
        """Set up take profit levels based on configuration"""
        self.entry_price = entry_price
        self.take_profit_levels = []
        self.take_profit_triggered = []
        
        if self.tp_config.get('use_percentage', True):
            # Percentage-based take profit levels
            percentages = self.tp_config.get('percentage_levels', [0.5, 1.0, 2.0])
            quantities = self.tp_config.get('quantity_per_level', [0.33, 0.33, 0.34])
            
            # Ensure we have matching percentages and quantities
            if len(percentages) != len(quantities):
                # Default to equal distribution if mismatch
                quantities = [1.0 / len(percentages)] * len(percentages)
            
            # Calculate actual price levels from percentages
            for i, percentage in enumerate(percentages):
                tp_price = entry_price * (1 + percentage / 100)
                self.take_profit_levels.append((tp_price, quantities[i]))
                self.take_profit_triggered.append(False)
                
            # Set the main take profit to the highest level for backward compatibility
            self.take_profit = self.take_profit_levels[-1][0] if self.take_profit_levels else None
            
            self.console.print(f"[green]Take profit levels set:[/green]")
            for i, (price, qty_pct) in enumerate(self.take_profit_levels):
                self.console.print(f"[green]Level {i+1}: ${price:.2f} ({percentages[i]}% gain) - {qty_pct*100:.1f}% of position[/green]")
        else:
            # Use the single take profit level (backward compatibility)
            if self.take_profit:
                self.take_profit_levels = [(self.take_profit, 1.0)]
                self.take_profit_triggered = [False]
                self.console.print(f"[green]Take profit set at: ${self.take_profit:.2f}[/green]")
    
    def check_stop_loss_take_profit(self, df):
        """Check if current position hit stop loss or take profit"""
        if self.current_position is None or self.stop_loss is None:
            return False
        
        current_close = df['close'].iloc[-1]
        
        if self.current_position == "long":
            # Check stop loss
            if current_close <= self.stop_loss:
                self.console.print(f"[bold red]STOP LOSS TRIGGERED at ${current_close:.2f}[/bold red]")
                return "sell_all"
            
            # Check take profit levels
            if self.take_profit_levels:
                for i, (tp_price, tp_quantity) in enumerate(self.take_profit_levels):
                    if not self.take_profit_triggered[i] and current_close >= tp_price:
                        self.take_profit_triggered[i] = True
                        self.console.print(f"[bold green]TAKE PROFIT TRIGGERED at ${current_close:.2f} (+{((current_close - self.entry_price) / self.entry_price) * 100:.2f}%)[/bold green]")
                        
                        # Sell the entire position when take profit is hit
                        return "sell_all"
            
            # For backward compatibility
            elif self.take_profit and current_close >= self.take_profit:
                self.console.print(f"[bold green]TAKE PROFIT TRIGGERED at ${current_close:.2f}[/bold green]")
                return "sell_all"
        
        return False
    
    def place_order(self, side, qty=None):
        """Place a market order"""
        try:
            # Get current price
            current_price = float(self.client.get_tickers(category="spot", symbol=self.symbol)["result"]["list"][0]["lastPrice"])
            
            # Get minimum order size and decimal places
            min_qty, min_order_amt, decimal_places = self.get_min_order_size(self.symbol)
            if min_qty is None:
                return None
            
            if side == "Buy":
                # For buy orders, use fixed amount from config
                if qty is None:
                    qty = self.trade_amount / current_price
                    qty = 10
                
                # Round to correct decimal places
                if decimal_places is not None:
                    multiplier = 10 ** decimal_places
                    qty = math.floor(qty * multiplier) / multiplier
                
                self.console.print(f"[yellow]Placing Buy order for {self.symbol}:[/yellow]")
                self.console.print(f"Price: {current_price:.2f}")
                self.console.print(f"Quantity: {qty}")
                
                # Store entry price for take profit calculations
                self.entry_price = current_price
                
            else:  # Sell order
                # For sell orders, use the available balance
                wallet = self.get_wallet_balance()
                base_currency = self.symbol[:-4] if self.symbol.endswith('USDT') else self.symbol.split('USDT')[0]
                
                if qty is None:
                    qty = wallet.get(base_currency, {}).get("free", 0)
                
                # Round to correct decimal places
                if decimal_places is not None:
                    multiplier = 10 ** decimal_places
                    qty = math.floor(qty * multiplier) / multiplier
                
                # Make sure quantity meets minimum requirements
                if qty < min_qty:
                    self.console.print(f"[yellow]Quantity {qty} is below minimum {min_qty}[/yellow]")
                    return None
                
                # Check if value meets minimum order amount
                value = qty * current_price
                if value < min_order_amt:
                    self.console.print(f"[yellow]Order value {value:.2f} USDT is below minimum {min_order_amt} USDT[/yellow]")
                    return None
                
                self.console.print(f"[yellow]Placing Sell order for {self.symbol}:[/yellow]")
                self.console.print(f"Price: {current_price:.2f}")
                self.console.print(f"Quantity: {qty}")
                self.console.print(f"Total Value: {value:.2f} USDT")
                
                # Calculate profit if we have an entry price
                if self.entry_price:
                    profit_pct = ((current_price - self.entry_price) / self.entry_price) * 100
                    profit_amount = (current_price - self.entry_price) * qty
                    self.console.print(f"[{'green' if profit_pct >= 0 else 'red'}]Profit: {profit_amount:.2f} USDT ({profit_pct:.2f}%)[/{'green' if profit_pct >= 0 else 'red'}]")
            
            # Place the order
            order = self.client.place_order(
                category="spot",
                symbol=self.symbol,
                side=side,
                orderType="MARKET",
                qty=str(qty)
            )
            
            self.console.print(f"[bold {'green' if side == 'Buy' else 'red'}]✓ {side} order placed for {qty} {self.symbol} at market price[/bold {'green' if side == 'Buy' else 'red'}]")
            
            # Log the transaction details
            if side == "Buy":
                self.console.print(f"[green]BTC purchased at ${current_price:.2f}[/green]")
                # Set up take profit levels after a buy
                self.setup_take_profit_levels(current_price)
                self.current_position = "long"
            else:
                self.console.print(f"[red]BTC sold at ${current_price:.2f}[/red]")
                
                # Check if this was a full sell or partial take profit
                wallet = self.get_wallet_balance()
                base_currency = self.symbol[:-4] if self.symbol.endswith('USDT') else self.symbol.split('USDT')[0]
                remaining_balance = wallet.get(base_currency, {}).get("free", 0)
                
                # If we sold everything or have very little left, reset all variables
                if remaining_balance < min_qty:
                    self.stop_loss = None
                    self.take_profit = None
                    self.take_profit_levels = []
                    self.take_profit_triggered = []
                    self.entry_price = None
                    self.current_position = None
            
            return order
            
        except Exception as e:
            self.console.print(f"[bold red]Error placing order: {str(e)}[/bold red]")
            return None
    
    def analyze_market(self):
        """Analyze market and execute trading strategy for the trading pair"""
        try:
            # Get market data
            df = self.get_klines()
            if df is None or len(df) < 30:  # Need enough data for indicators
                self.console.print("[yellow]Not enough data for analysis[/yellow]")
                return
            
            # Calculate indicators
            df = self.calculate_indicators(df)
            if df is None:
                return
            
            # Get current values for display
            current_close = df['close'].iloc[-1]
            current_rsi = df['rsi'].iloc[-1]
            current_k = df['stoch_k'].iloc[-1]
            current_d = df['stoch_d'].iloc[-1]
            bb_upper = df['bb_upper'].iloc[-1]
            bb_middle = df['bb_middle'].iloc[-1]
            bb_lower = df['bb_lower'].iloc[-1]
            
            # Display current market status
            self.console.print(f"[cyan]BTCUSDT - Price: ${current_close:.2f}[/cyan]")
            self.console.print(f"RSI: {current_rsi:.2f}, Stoch %K: {current_k:.2f}, %D: {current_d:.2f}")
            self.console.print(f"BB Upper: ${bb_upper:.2f}, Middle: ${bb_middle:.2f}, Lower: ${bb_lower:.2f}")
            
            # Check if we need to close position due to stop loss or take profit
            sl_tp_action = self.check_stop_loss_take_profit(df)
            if sl_tp_action == "sell_all":
                self.place_order("Sell")
                return
            elif isinstance(sl_tp_action, tuple) and sl_tp_action[0] == "sell_partial":
                self.place_order("Sell", sl_tp_action[1])
                return
            
            # Check wallet balance to see if we have BTC
            wallet = self.get_wallet_balance()
            btc_balance = wallet.get("BTC", {}).get("total", 0)
            
            # Display current BTC balance if any
            if btc_balance > 0:
                self.console.print(f"[yellow]Current BTC balance: {btc_balance} BTC[/yellow]")
            # Check for buy signals
            oversold_buy, oversold_tp, oversold_sl = self.check_oversold_buy_signal(df)
            if oversold_buy:
                self.last_signal = "oversold_buy"
                self.console.print(f"[bold green]Oversold buy signal detected - Buying {self.base_asset}[/bold green]")
                order = self.place_order("Buy")
                if order is not None:
                    # Store the indicator-based take profit level for reference
                    self.take_profit = oversold_tp
                    self.stop_loss = oversold_sl
                    # Setup take profit levels happens inside place_order
                return
            
            trend_buy, trend_tp, trend_sl = self.check_trend_buy_signal(df)
            if trend_buy:
                self.last_signal = "trend_buy"
                self.console.print(f"[bold green]Trend buy signal detected - Buying {self.base_asset}[/bold green]")
                order = self.place_order("Buy")
                if order is not None:
                    # Store the indicator-based take profit level for reference
                    self.take_profit = trend_tp
                    self.stop_loss = trend_sl
                    # Setup take profit levels happens inside place_order
                return
            
            # Check for sell signals
            overbought_sell, overbought_tp, overbought_sl = self.check_overbought_sell_signal(df)
            if overbought_sell:
                self.last_signal = "overbought_sell"
                self.console.print(f"[bold red]Overbought sell signal detected - Selling {self.base_asset}[/bold red]")
                order = self.place_order("Sell")
                return
            
            trend_sell, trend_sell_tp, trend_sell_sl = self.check_trend_sell_signal(df)
            if trend_sell:
                self.last_signal = "trend_sell"
                self.console.print(f"[bold red]Trend sell signal detected - Selling {self.base_asset}[/bold red]")
                order = self.place_order("Sell")
                return
            
            # No signals
            self.console.print("[white]No trading signals detected[/white]")
            
        except Exception as e:
            self.console.print(f"[bold red]Error analyzing market: {str(e)}[/bold red]")
            import traceback
            traceback.print_exc()
    
    def run(self):
        """Main bot loop"""
        self.console.print(Panel.fit(f"[bold green]{self.symbol} Trading Bot Started[/bold green]", title="Status"))
        
        while True:
            try:
                self.console.print(f"\n[bold blue]Analyzing {self.symbol} market...[/bold blue]")
                self.analyze_market()
                
                # Wait before next iteration
                self.console.print(f"[yellow]Waiting {self.update_interval} seconds before next analysis...[/yellow]")
                time.sleep(self.update_interval)
                
            except Exception as e:
                self.console.print(f"[bold red]Error: {str(e)}[/bold red]")
                time.sleep(5)

if __name__ == "__main__":
    load_dotenv()
    
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(description='Trading Bot for Bybit')
    parser.add_argument('symbol', nargs='?', type=str, default="BTC", help='Trading pair symbol (e.g., BTC, ETH, DOGE) - will be paired with USDT')
    parser.add_argument('--testnet', action='store_true', help='Use testnet instead of mainnet')
    parser.add_argument('--config', type=str, default='config.json', help='Path to configuration file')
    
    args = parser.parse_args()
    
    # Format the symbol properly (add USDT suffix if not already present)
    symbol = args.symbol.upper()
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"
    
    api_key = os.getenv('BYBIT_API_KEY')
    api_secret = os.getenv('BYBIT_API_SECRET')
    
    if not api_key or not api_secret:
        print("Please set BYBIT_API_KEY and BYBIT_API_SECRET in .env file")
        exit(1)
    
    console = Console()
    console.print(f"[bold blue]Starting {symbol} Trading Bot...[/bold blue]")
    console.print(f"[cyan]Using {'testnet' if args.testnet else 'mainnet'}[/cyan]")
    
    try:
        bot = TradingBot(api_key, api_secret, symbol=symbol, config_path=args.config, testnet=args.testnet)
        bot.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]Error starting bot: {str(e)}[/bold red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
