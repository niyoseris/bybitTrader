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
        logging.FileHandler("bb_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class BBTradingBot:
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
        self.current_position = None  # None: no position, "long": long position
        self.entry_price = None
        self.take_profit = None
        self.prev_close = None
        
        # Get last trade information
        self.get_last_trade_info()
        
    def load_config(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                config_json = json.load(f)
                
            # We're only using Bollinger Bands for this strategy
            self.active_indicators = {
                'BBANDS': True
            }
            
            self.indicator_params = config_json['indicators']
            self.trade_amount = config_json['trading']['amount']
            self.update_interval = config_json['trading']['update_interval']
            self.kline_interval = str(config_json['trading']['kline']['interval'])
            self.kline_limit = int(config_json['trading']['kline']['limit'])
            
            # Load take profit settings
            self.take_profit_percentage = config_json['trading'].get('take_profit_percentage', 0.5)
            
            # Kline interval validation
            valid_intervals = ['1', '3', '5', '15', '30', '60', '120', '240', '360', '720', '1440']
            if self.kline_interval not in valid_intervals:
                raise ValueError(f"Invalid kline interval. Valid values: {', '.join(valid_intervals)}")
            
            self.console.print(f"[green]Configuration loaded successfully from {config_path}[/green]")
            self.console.print(f"[blue]Using {self.kline_interval} minute candles, fetching last {self.kline_limit} candles[/blue]")
            
        except Exception as e:
            self.console.print(f"[bold red]Error loading config: {str(e)}[/bold red]")
            raise
    
    def get_last_trade_info(self):
        """Get information about the last trade for this symbol"""
        try:
            # Get execution history instead of trade history
            trade_history = self.client.get_executions(
                category="spot",
                symbol=self.symbol,
                limit=1  # Just get the most recent execution
            )
            
            if trade_history.get("retCode") == 0 and trade_history.get("result", {}).get("list"):
                last_trade = trade_history["result"]["list"][0]
                
                # Extract relevant information
                side = last_trade.get("side")
                exec_price = float(last_trade.get("execPrice", 0))
                exec_qty = float(last_trade.get("execQty", 0))
                exec_time = datetime.fromtimestamp(int(last_trade.get("execTime", 0)) / 1000)
                
                self.console.print(f"[bold blue]Last trade found:[/bold blue]")
                self.console.print(f"Side: {side}, Price: ${exec_price:.4f}, Quantity: {exec_qty:.4f}, Time: {exec_time}")
                
                # If the last trade was a buy, set the current position
                if side == "Buy":
                    self.current_position = "long"
                    self.entry_price = exec_price
                    self.take_profit = self.calculate_take_profit(exec_price)
                    self.console.print(f"[green]Setting current position based on last trade[/green]")
                    self.console.print(f"[green]Entry Price: ${self.entry_price:.4f}, Take Profit: ${self.take_profit:.4f} (+{self.take_profit_percentage:.4f}%)[/green]")
                
            else:
                self.console.print("[yellow]No recent trades found for this symbol[/yellow]")
                
        except Exception as e:
            self.console.print(f"[bold red]Error getting last trade info: {str(e)}[/bold red]")
    
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
            df['timestamp'] = pd.to_datetime(df['timestamp'].astype(float), unit='ms')
            
            # Sort by timestamp (newest data last)
            df = df.sort_values('timestamp')
            
            return df
        except Exception as e:
            self.console.print(f"[bold red]Error fetching klines: {str(e)}[/bold red]")
            return None
    
    def calculate_indicators(self, df):
        """Calculate Bollinger Bands indicator"""
        try:
            # Calculate Bollinger Bands with standard settings (length=20, multiplier=2)
            bb = BollingerBands(df['close'], window=20, window_dev=2)
            df['bb_upper'] = bb.bollinger_hband()
            df['bb_middle'] = bb.bollinger_mavg()
            df['bb_lower'] = bb.bollinger_lband()
            
            # Store previous close for comparison
            if len(df) > 1:
                self.prev_close = df['close'].iloc[-2]
            
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
    
    def check_take_profit(self, df):
        """Check if current position hit take profit"""
        if self.current_position is None or self.take_profit is None:
            return False
        
        current_close = df['close'].iloc[-1]
        
        if self.current_position == "long" and current_close >= self.take_profit:
            self.console.print(f"[bold green]TAKE PROFIT TRIGGERED at ${current_close:.4f} (+{((current_close - self.entry_price) / self.entry_price) * 100:.4f}%)[/bold green]")
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
                    qty = 30  # Fixed quantity for simplicity
                
                # Round to correct decimal places
                if decimal_places is not None:
                    multiplier = 10 ** decimal_places
                    qty = math.floor(qty * multiplier) / multiplier
                
                self.console.print(f"[yellow]Placing Buy order for {self.symbol}:[/yellow]")
                self.console.print(f"Price: {current_price:.4f}")
                self.console.print(f"Quantity: {qty:.4f}")
                
                # Place the order
                order = self.client.place_order(
                    category="spot",
                    symbol=self.symbol,
                    side="Buy",
                    orderType="Market",
                    qty=str(qty)
                )
                
                if order.get("retCode") == 0:
                    self.console.print(f"[bold green]Buy order placed successfully![/bold green]")
                    self.current_position = "long"
                    self.entry_price = current_price
                    self.take_profit = self.calculate_take_profit(current_price)
                    self.console.print(f"[green]Take profit set at: ${self.take_profit:.4f} (+{self.take_profit_percentage:.4f}%)[/green]")
                else:
                    self.console.print(f"[bold red]Error placing buy order: {order.get('retMsg')}[/bold red]")
                    return None
                
            elif side == "Sell":
                # Get wallet balance to determine how much to sell
                wallet = self.get_wallet_balance()
                btc_balance = wallet.get(self.base_asset, {}).get("free", 0)
                
                if btc_balance <= 0:
                    self.console.print(f"[yellow]No {self.base_asset} balance available to sell[/yellow]")
                    return None
                
                # If qty not specified, sell all available balance
                if qty is None:
                    qty = btc_balance
                
                # Round to correct decimal places
                if decimal_places is not None:
                    multiplier = 10 ** decimal_places
                    qty = math.floor(qty * multiplier) / multiplier
                
                self.console.print(f"[yellow]Placing Sell order for {self.symbol}:[/yellow]")
                self.console.print(f"Price: {current_price:.4f}")
                self.console.print(f"Quantity: {qty:.4f}")
                
                # Place the order
                order = self.client.place_order(
                    category="spot",
                    symbol=self.symbol,
                    side="Sell",
                    orderType="Market",
                    qty=str(qty)
                )
                
                if order.get("retCode") == 0:
                    self.console.print(f"[bold green]Sell order placed successfully![/bold green]")
                    self.take_profit = None
                    self.entry_price = None
                    self.current_position = None
                else:
                    self.console.print(f"[bold red]Error placing sell order: {order.get('retMsg')}[/bold red]")
                    return None
            
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
            bb_upper = df['bb_upper'].iloc[-1]
            bb_middle = df['bb_middle'].iloc[-1]
            bb_lower = df['bb_lower'].iloc[-1]
            
            # Display current market status
            self.console.print(f"[cyan]{self.symbol} - Price: ${current_close:.4f}[/cyan]")
            self.console.print(f"BB Upper: ${bb_upper:.4f}, Middle: ${bb_middle:.4f}, Lower: ${bb_lower:.4f}")
            
            # Display take profit price if in a position
            if self.current_position == "long" and self.take_profit is not None:
                self.console.print(f"[green]Current Take Profit Price: ${self.take_profit:.4f} (+{self.take_profit_percentage:.4f}%)[/green]")
            
            # Check if we need to close position due to take profit
            tp_action = self.check_take_profit(df)
            if tp_action == "sell_all":
                self.place_order("Sell")
                return
            
            # Check wallet balance to see if we have the base asset
            wallet = self.get_wallet_balance()
            base_balance = wallet.get(self.base_asset, {}).get("total", 0)
            
            # Display current base asset balance if any
            if base_balance > 0:
                self.console.print(f"[yellow]Current {self.base_asset} balance: {base_balance} {self.base_asset}[/yellow]")
                
                # If we have a position, show entry and take profit
                if self.entry_price is not None:
                    current_profit_pct = ((current_close - self.entry_price) / self.entry_price) * 100
                    profit_color = "green" if current_profit_pct >= 0 else "red"
                    self.console.print(f"[yellow]Entry Price: ${self.entry_price:.4f}, Take Profit: ${self.take_profit:.4f} (+{self.take_profit_percentage:.4f}%)[/yellow]")
                    self.console.print(f"[{profit_color}]Current P/L: {current_profit_pct:.4f}%[/{profit_color}]")
                
            # Check for buy signal - only when price is below lower Bollinger Band and we don't have a position
            if self.is_price_below_bb_lower(df) and (self.current_position is None):
                # Only buy if we don't already have a position
                self.console.print(f"[bold green]BUY SIGNAL: Price below lower Bollinger Band[/bold green]")
                self.console.print(f"Price: ${current_close:.4f}, Lower BB: ${bb_lower:.4f}")
                self.console.print(f"Take Profit will be set at: ${current_close * (1 + self.take_profit_percentage / 100):.4f} (+{self.take_profit_percentage:.4f}%)")
                order = self.place_order("Buy")
                return
            
            # No signals
            self.console.print("[white]No trading signals detected[/white]")
            
        except Exception as e:
            self.console.print(f"[bold red]Error analyzing market: {str(e)}[/bold red]")
            import traceback
            traceback.print_exc()
    
    def run(self):
        """Main bot loop"""
        self.console.print(Panel.fit(f"[bold green]{self.symbol} Bollinger Band Trading Bot Started[/bold green]", title="Status"))
        
        # Display current position if any
        if self.current_position == "long" and self.entry_price is not None:
            self.console.print(Panel.fit(f"[bold yellow]Current Position: LONG\nEntry Price: ${self.entry_price:.4f}\nTake Profit: ${self.take_profit:.4f} (+{self.take_profit_percentage:.4f}%)[/bold yellow]", title="Position"))
        
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
    
    parser = argparse.ArgumentParser(description='Bollinger Band Trading Bot for Bybit')
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
    console.print(f"[bold blue]Starting {symbol} Bollinger Band Trading Bot...[/bold blue]")
    console.print(f"[cyan]Using {'testnet' if args.testnet else 'mainnet'}[/cyan]")
    
    try:
        bot = BBTradingBot(api_key, api_secret, symbol=symbol, config_path=args.config, testnet=args.testnet)
        bot.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]Error starting bot: {str(e)}[/bold red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
