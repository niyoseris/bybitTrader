import os
import json
import time
import numpy as np
import pandas as pd
import threading
from datetime import datetime
from threading import Lock
from pybit.unified_trading import HTTP
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
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
        logging.FileHandler("bbmulti_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class BBMultiTradingBot:
    def __init__(self, api_key, api_secret, symbols=["ETHUSDT", "SOLUSDT", "SUIUSDT", "ADAUSDT", "XRPUSDT", "DOGEUSDT", "SUSDT", "HAEDALUSDT", "TRUMPUSDT", "VIRTUALUSDT", "AVAXUSDT", "APEXUSDT", "WALUSDT", "PEPEUSDT", "TONUSDT"], config_path='config.json', testnet=True):
        self.client = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet
        )
        self.console = Console()
        self.market_data_lock = Lock()
        self.load_config(config_path)
        
        # Initialize symbols
        self.symbols = symbols
        self.base_assets = {}
        self.current_positions = {}
        self.entry_prices = {}
        self.take_profits = {}
        self.prev_closes = {}
        
        # Initialize data for each symbol
        for symbol in self.symbols:
            self.base_assets[symbol] = symbol.replace("USDT", "")  # Extract base asset (e.g., BTC from BTCUSDT)
            self.current_positions[symbol] = None  # None: no position, "long": long position
            self.entry_prices[symbol] = None
            self.take_profits[symbol] = None
            self.prev_closes[symbol] = None
            
            # Get last trade information for each symbol
            self.get_last_trade_info(symbol)
        
    def load_config(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                config_json = json.load(f)
                
            # Using Bollinger Bands and RSI for this strategy
            self.active_indicators = {
                'BBANDS': True,
                'RSI': True
            }
            
            self.indicator_params = config_json['indicators']
            self.trade_amount = config_json['trading']['amount']
            self.update_interval = config_json['trading']['update_interval']
            self.kline_interval = str(config_json['trading']['kline']['interval'])
            self.kline_limit = int(config_json['trading']['kline']['limit'])
            
            # Load take profit settings
            self.take_profit_percentage = config_json['trading'].get('take_profit_percentage', 0.5)
            
            # Load display formatting settings
            display_settings = config_json['trading'].get('display', {})
            self.enable_decimal_formatting = display_settings.get('enable_decimal_formatting', True)
            self.price_decimal_places = display_settings.get('price_decimal_places', 4)
            self.percentage_decimal_places = display_settings.get('percentage_decimal_places', 2)
            
            # Kline interval validation
            valid_intervals = ['1', '3', '5', '15', '30', '60', '120', '240', '360', '720', '1440']
            if self.kline_interval not in valid_intervals:
                raise ValueError(f"Invalid kline interval. Valid values: {', '.join(valid_intervals)}")
            
            self.console.print(f"[green]Configuration loaded successfully from {config_path}[/green]")
            self.console.print(f"[blue]Using {self.kline_interval} minute candles, fetching last {self.kline_limit} candles[/blue]")
            if self.enable_decimal_formatting:
                self.console.print(f"[blue]Display formatting: Price decimals: {self.price_decimal_places}, Percentage decimals: {self.percentage_decimal_places}[/blue]")
        
        except Exception as e:
            self.console.print(f"[bold red]Error loading config: {str(e)}[/bold red]")
            raise
    
    def get_last_trade_info(self, symbol):
        """Get information about the last trade for a specific symbol"""
        try:
            # Get execution history instead of trade history
            trade_history = self.client.get_executions(
                category="spot",
                symbol=symbol,
                limit=1  # Just get the most recent execution
            )
            
            if trade_history.get("retCode") == 0 and trade_history.get("result", {}).get("list"):
                last_trade = trade_history["result"]["list"][0]
                
                # Extract relevant information
                side = last_trade.get("side")
                exec_price = float(last_trade.get("execPrice", 0))
                exec_qty = float(last_trade.get("execQty", 0))
                exec_time = datetime.fromtimestamp(int(last_trade.get("execTime", 0)) / 1000)
                
                self.console.print(f"[bold blue]Last trade found for {symbol}:[/bold blue]")
                self.console.print(f"Side: {side}, Price: ${exec_price:.4f}, Quantity: {exec_qty:.4f}, Time: {exec_time}")
                
                # If the last trade was a buy, set the current position
                if side == "Buy":
                    self.current_positions[symbol] = "long"
                    self.entry_prices[symbol] = exec_price
                    self.take_profits[symbol] = self.calculate_take_profit(exec_price)
                    self.console.print(f"[green]Setting current position based on last trade for {symbol}[/green]")
                    self.console.print(f"[green]Entry Price: ${self.entry_prices[symbol]:.4f}, Take Profit: ${self.take_profits[symbol]:.4f} (+{self.take_profit_percentage:.4f}%)[/green]")
                
            else:
                self.console.print(f"[yellow]No recent trades found for {symbol}[/yellow]")
                
        except Exception as e:
            self.console.print(f"[bold red]Error getting last trade info for {symbol}: {str(e)}[/bold red]")
    
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
    
    def get_klines(self, symbol):
        """Get historical klines/candlestick data for the trading pair"""
        try:
            klines = self.client.get_kline(
                category="spot",
                symbol=symbol,
                interval=self.kline_interval,
                limit=self.kline_limit
            )
            
            df = pd.DataFrame(klines["result"]["list"], 
                             columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
            
            # Convert timestamp to datetime for easier analysis
            df['timestamp'] = pd.to_datetime(df['timestamp'].astype(float), unit='ms')
            
            # Sort by timestamp (newest data last)
            df = df.sort_values('timestamp')
            
            return df
        except Exception as e:
            self.console.print(f"[bold red]Error fetching klines for {symbol}: {str(e)}[/bold red]")
            return None
    
    def calculate_indicators(self, df):
        """Calculate Bollinger Bands and RSI indicators"""
        try:
            # Calculate Bollinger Bands with standard settings (length=20, multiplier=2)
            bb = BollingerBands(df['close'], window=20, window_dev=2)
            df['bb_upper'] = bb.bollinger_hband()
            df['bb_middle'] = bb.bollinger_mavg()
            df['bb_lower'] = bb.bollinger_lband()
            
            # Calculate RSI with RMA (Wilder's) smoothing
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
            
            return df
        except Exception as e:
            self.console.print(f"[bold red]Error calculating indicators: {str(e)}[/bold red]")
            return None
    
    def is_price_below_bb_lower(self, df):
        """Check if price is below the lower Bollinger Band"""
        current_close = df['close'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        
        # Price is strictly below the lower band
        return current_close < bb_lower
    
    def calculate_take_profit(self, entry_price):
        """Calculate take profit price based on percentage from config"""
        return entry_price * (1 + self.take_profit_percentage / 100)
    
    def check_take_profit(self, symbol, df):
        """Check if current position hit take profit"""
        if self.current_positions[symbol] is None or self.take_profits[symbol] is None:
            return False
        
        current_close = df['close'].iloc[-1]
        
        if self.current_positions[symbol] == "long" and current_close >= self.take_profits[symbol]:
            self.console.print(f"[bold green]{symbol} TAKE PROFIT TRIGGERED at ${current_close:.4f} (+{((current_close - self.entry_prices[symbol]) / self.entry_prices[symbol]) * 100:.4f}%)[/bold green]")
            return "sell_all"
        
        return False
    
    def place_order(self, symbol, side, qty=None):
        """Place a market order"""
        try:
            # Get current price
            current_price = float(self.client.get_tickers(category="spot", symbol=symbol)["result"]["list"][0]["lastPrice"])
            
            # Get minimum order size and decimal places
            min_qty, min_order_amt, decimal_places = self.get_min_order_size(symbol)
            if min_qty is None:
                return None
            
            if side == "Buy":
                # For buy orders, use fixed amount from config
                if qty is None:
                    qty = self.trade_amount / current_price
                    qty = 10  # Fixed quantity for simplicity
                
                # Round to correct decimal places
                if decimal_places is not None:
                    multiplier = 10 ** decimal_places
                    qty = math.floor(qty * multiplier) / multiplier
                
                self.console.print(f"[yellow]Placing Buy order for {symbol}:[/yellow]")
                self.console.print(f"Price: {current_price:.4f}")
                self.console.print(f"Quantity: {qty:.4f}")
                
                # Place the order
                order = self.client.place_order(
                    category="spot",
                    symbol=symbol,
                    side="Buy",
                    orderType="Market",
                    qty=str(qty)
                )
                
                if order.get("retCode") == 0:
                    self.console.print(f"[bold green]Buy order placed successfully for {symbol}![/bold green]")
                    self.current_positions[symbol] = "long"
                    self.entry_prices[symbol] = current_price
                    self.take_profits[symbol] = self.calculate_take_profit(current_price)
                    self.console.print(f"[green]Take profit set at: ${self.take_profits[symbol]:.4f} (+{self.take_profit_percentage:.4f}%)[/green]")
                else:
                    self.console.print(f"[bold red]Error placing buy order for {symbol}: {order.get('retMsg')}[/bold red]")
                    return None
                
            elif side == "Sell":
                # Get wallet balance to determine how much to sell
                wallet = self.get_wallet_balance()
                base_asset = self.base_assets[symbol]
                base_balance = wallet.get(base_asset, {}).get("free", 0)
                
                if base_balance <= 0:
                    self.console.print(f"[yellow]No {base_asset} balance available to sell[/yellow]")
                    return None
                
                # If qty not specified, sell all available balance
                if qty is None:
                    qty = base_balance
                
                # Round to correct decimal places
                if decimal_places is not None:
                    multiplier = 10 ** decimal_places
                    qty = math.floor(qty * multiplier) / multiplier
                
                self.console.print(f"[yellow]Placing Sell order for {symbol}:[/yellow]")
                self.console.print(f"Price: {current_price:.4f}")
                self.console.print(f"Quantity: {qty:.4f}")
                
                # Place the order
                order = self.client.place_order(
                    category="spot",
                    symbol=symbol,
                    side="Sell",
                    orderType="Market",
                    qty=str(qty)
                )
                
                if order.get("retCode") == 0:
                    self.console.print(f"[bold green]Sell order placed successfully for {symbol}![/bold green]")
                    self.take_profits[symbol] = None
                    self.entry_prices[symbol] = None
                    self.current_positions[symbol] = None
                else:
                    self.console.print(f"[bold red]Error placing sell order for {symbol}: {order.get('retMsg')}[/bold red]")
                    return None
            
            return order
            
        except Exception as e:
            self.console.print(f"[bold red]Error placing order for {symbol}: {str(e)}[/bold red]")
            return None
    
    def format_value(self, value, is_price=True):
        """Format a value according to the user's decimal place preferences"""
        if not self.enable_decimal_formatting:
            # Default formatting if disabled
            return f"{value:.4f}" if is_price else f"{value:.2f}"
        
        # Use the configured decimal places
        decimal_places = self.price_decimal_places if is_price else self.percentage_decimal_places
        return f"{value:.{decimal_places}f}"
    
    def analyze_market(self, symbol):
        """Analyze market and execute trading strategy for a specific trading pair"""
        try:
            # Get market data
            df = self.get_klines(symbol)
            if df is None or len(df) < 30:  # Need enough data for indicators
                self.console.print(f"[yellow]Not enough data for analysis of {symbol}[/yellow]")
                return
            
            # Calculate indicators
            df = self.calculate_indicators(df)
            if df is None:
                return
            
            # Get current values for display
            current_close = df['close'].iloc[-1]
            bb_upper = df['bb_upper'].iloc[-1]
            bb_middle = df['bb_middle'].iloc[-1]
            bb_lower = df['bb_lower'].iloc[-1]
            
            # Display current market status
            current_rsi = df['rsi'].iloc[-1]
            self.console.print(f"[cyan]{symbol} - Price: ${self.format_value(current_close)}[/cyan]")
            self.console.print(f"BB Upper: ${self.format_value(bb_upper)}, Middle: ${self.format_value(bb_middle)}, Lower: ${self.format_value(bb_lower)}")
            self.console.print(f"RSI: {self.format_value(current_rsi, is_price=False)}")
            
            # Display take profit price if in a position
            if self.current_positions[symbol] == "long" and self.take_profits[symbol] is not None:
                self.console.print(f"[green]Current Take Profit Price for {symbol}: ${self.format_value(self.take_profits[symbol])} (+{self.format_value(self.take_profit_percentage, is_price=False)}%)[/green]")
            
            # Check if we need to close position due to take profit
            tp_action = self.check_take_profit(symbol, df)
            if tp_action == "sell_all":
                self.place_order(symbol, "Sell")
                return
            
            # Check wallet balance to see if we have the base asset
            wallet = self.get_wallet_balance()
            base_asset = self.base_assets[symbol]
            base_balance = wallet.get(base_asset, {}).get("total", 0)
            
            # Display current base asset balance if any
            if base_balance > 0:
                self.console.print(f"[yellow]Current {base_asset} balance: {base_balance} {base_asset}[/yellow]")
                
                # If we have a position, show entry and take profit
                if self.entry_prices[symbol] is not None:
                    current_profit_pct = ((current_close - self.entry_prices[symbol]) / self.entry_prices[symbol]) * 100
                    profit_color = "green" if current_profit_pct >= 0 else "red"
                    self.console.print(f"[yellow]Entry Price: ${self.format_value(self.entry_prices[symbol])}, Take Profit: ${self.format_value(self.take_profits[symbol])} (+{self.format_value(self.take_profit_percentage, is_price=False)}%)[/yellow]")
                    self.console.print(f"[{profit_color}]Current P/L: {self.format_value(current_profit_pct, is_price=False)}%[/{profit_color}]")
            
            # Check for buy signal - only when price is below lower Bollinger Band, RSI is below 30, and we don't have a position
            if self.is_price_below_bb_lower(df) and current_rsi < 30 and (self.current_positions[symbol] is None):
                # Only buy if we don't already have a position
                self.console.print(f"[bold green]BUY SIGNAL for {symbol}: Price below lower Bollinger Band and RSI below 30[/bold green]")
                self.console.print(f"Price: ${self.format_value(current_close)}, Lower BB: ${self.format_value(bb_lower)}, RSI: {self.format_value(current_rsi, is_price=False)}")
                take_profit_price = current_close * (1 + self.take_profit_percentage / 100)
                self.console.print(f"Take Profit will be set at: ${self.format_value(take_profit_price)} (+{self.format_value(self.take_profit_percentage, is_price=False)}%)")
                order = self.place_order(symbol, "Buy")
                return
            
            # No signals
            self.console.print(f"[white]No trading signals detected for {symbol}[/white]")
            
        except Exception as e:
            self.console.print(f"[bold red]Error analyzing market for {symbol}: {str(e)}[/bold red]")
            import traceback
            traceback.print_exc()
    
    def get_high_volume_pairs(self, min_volume=1000000, excluded_coins=None):
        """Get trading pairs with 24h volume above the specified minimum and matching the configured decimal places"""
        try:
            # Initialize excluded coins list if None
            if excluded_coins is None:
                excluded_coins = []
            
            # Convert all excluded coins to uppercase for comparison
            excluded_coins = [coin.upper() for coin in excluded_coins]
            
            # Get tickers for all symbols
            tickers = self.client.get_tickers(category="spot")
            high_volume_pairs = []
            excluded_pairs = []
            matching_decimal_pairs = []
            
            # Get the target decimal places from config (default to 4 if not using custom formatting)
            target_decimal_places = self.price_decimal_places if self.enable_decimal_formatting else 4
            
            if tickers.get("retCode") == 0 and tickers.get("result", {}).get("list"):
                for ticker in tickers["result"]["list"]:
                    symbol = ticker.get("symbol")
                    # Only consider USDT pairs
                    if symbol and symbol.endswith("USDT"):
                        # Extract the base coin (e.g., BTC from BTCUSDT)
                        base_coin = symbol.replace("USDT", "")
                        
                        # Skip if the base coin is in the excluded list
                        if base_coin in excluded_coins:
                            volume_24h = float(ticker.get("turnover24h", 0))
                            if volume_24h >= min_volume:
                                excluded_pairs.append((symbol, volume_24h))
                            continue
                        
                        # Convert volume to float and check against minimum
                        volume_24h = float(ticker.get("turnover24h", 0))
                        if volume_24h >= min_volume:
                            # Check decimal places
                            min_qty, min_order_amt, decimal_places = self.get_min_order_size(symbol)
                            if decimal_places == target_decimal_places:
                                matching_decimal_pairs.append(symbol)
                                self.console.print(f"[green]Added {symbol} - 24h Volume: ${volume_24h:.2f}, Decimal Places: {decimal_places}[/green]")
                            else:
                                self.console.print(f"[yellow]Skipping {symbol} - 24h Volume: ${volume_24h:.2f}, Decimal Places: {decimal_places} (not {target_decimal_places})[/yellow]")
                                excluded_pairs.append((symbol, volume_24h))
            
            # Report on excluded pairs
            if excluded_pairs:
                self.console.print(f"[yellow]Excluded {len(excluded_pairs)} high volume pairs:[/yellow]")
                for pair, volume in excluded_pairs:
                    self.console.print(f"[yellow]Excluded {pair} - 24h Volume: ${volume:.2f}[/yellow]")
            
            self.console.print(f"[bold blue]Found {len(matching_decimal_pairs)} pairs with 24h volume above ${min_volume:,} and exactly {target_decimal_places} decimal places[/bold blue]")
            return matching_decimal_pairs
        except Exception as e:
            self.console.print(f"[bold red]Error getting high volume pairs: {str(e)}[/bold red]")
            return []
    
    def analyze_all_markets(self):
        """Analyze all markets in the symbols list"""
        for symbol in self.symbols:
            self.console.print(f"\n[bold blue]Analyzing {symbol} market...[/bold blue]")
            self.analyze_market(symbol)
    
    def run(self):
        """Main bot loop"""
        self.console.print(Panel.fit(f"[bold green]Multi-Coin Bollinger Band Trading Bot Started[/bold green]", title="Status"))
        self.console.print(f"[bold blue]Monitoring symbols: {', '.join(self.symbols)}[/bold blue]")
        
        # Display current positions if any
        for symbol in self.symbols:
            if self.current_positions[symbol] == "long" and self.entry_prices[symbol] is not None:
                self.console.print(Panel.fit(f"[bold yellow]Current Position for {symbol}: LONG\nEntry Price: ${self.entry_prices[symbol]:.4f}\nTake Profit: ${self.take_profits[symbol]:.4f} (+{self.take_profit_percentage:.4f}%)[/bold yellow]", title=f"{symbol} Position"))
        
        while True:
            try:
                self.analyze_all_markets()
                
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
    
    parser = argparse.ArgumentParser(description='Multi-Coin Bollinger Band Trading Bot for Bybit')
    parser.add_argument('--symbols', type=str, default="ETH,SOL,SUI,ADA,XRP,DOGE,S,HAEDAL,TRUMP,VIRTUAL,AVAX,APEX,WAL,PEPE,TON,ONDO", help='Comma-separated list of trading symbols (e.g., BTC,ETH,SOL) - will be paired with USDT')
    parser.add_argument('--high-volume', action='store_true', help='Use high volume pairs (24h volume > 1,000,000 USDT) instead of specified symbols')
    parser.add_argument('--min-volume', type=float, default=1000000, help='Minimum 24h volume for high volume pairs (default: 1,000,000 USDT)')
    parser.add_argument('--exclude', type=str, default="BTC", help='Comma-separated list of coins to exclude (e.g., BTC,ETH)')
    parser.add_argument('--testnet', action='store_true', help='Use testnet instead of mainnet')
    parser.add_argument('--config', type=str, default='config.json', help='Path to configuration file')
    
    args = parser.parse_args()
    
    # Parse the symbols
    symbol_list = [s.strip().upper() for s in args.symbols.split(',')]
    # Format the symbols properly (add USDT suffix if not already present)
    symbols = []
    for symbol in symbol_list:
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        symbols.append(symbol)
    
    api_key = os.getenv('BYBIT_API_KEY')
    api_secret = os.getenv('BYBIT_API_SECRET')
    
    if not api_key or not api_secret:
        print("Please set BYBIT_API_KEY and BYBIT_API_SECRET in .env file")
        exit(1)
    
    console = Console()
    console.print(f"[bold blue]Starting Multi-Coin Bollinger Band Trading Bot...[/bold blue]")
    console.print(f"[cyan]Using {'testnet' if args.testnet else 'mainnet'}[/cyan]")
    
    try:
        # Initialize bot with default symbols first
        bot = BBMultiTradingBot(api_key, api_secret, symbols=symbols, config_path=args.config, testnet=args.testnet)
        
        # If high-volume flag is set, replace symbols with high volume pairs
        if args.high_volume:
            # Parse excluded coins
            excluded_coins = [coin.strip() for coin in args.exclude.split(',')] if args.exclude else []
            if excluded_coins:
                console.print(f"[yellow]Will exclude these coins: {', '.join(excluded_coins)}[/yellow]")
            
            console.print(f"[yellow]Fetching high volume pairs (minimum 24h volume: ${args.min_volume:,})...[/yellow]")
            high_volume_pairs = bot.get_high_volume_pairs(min_volume=args.min_volume, excluded_coins=excluded_coins)
            
            if high_volume_pairs:
                # Reinitialize bot with high volume pairs
                bot = BBMultiTradingBot(api_key, api_secret, symbols=high_volume_pairs, config_path=args.config, testnet=args.testnet)
            else:
                console.print(f"[bold red]No high volume pairs found. Using default symbols.[/bold red]")
        else:
            console.print(f"[cyan]Monitoring symbols: {', '.join(symbols)}[/cyan]")
        
        bot.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]Error starting bot: {str(e)}[/bold red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
