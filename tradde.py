import os
import json
import time
import numpy as np
import pandas as pd
import threading
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from threading import Lock
from pybit.unified_trading import HTTP
from ta.trend import SMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from dotenv import load_dotenv
import logging
import config
from data_collector import fetch_klines
# Import functions from btcusdt_rsi_simple.py
from btcusdt_rsi_simple import calculate_rsi_with_rma, get_current_rsi, fetch_btcusdt_data

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, api_key, api_secret, config_path='config.json', testnet=True):
        self.client = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet
        )
        self.console = Console()
        self.market_data_lock = Lock()
        self.load_config(config_path)
        
    def load_config(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                config_json = json.load(f)
                
            # We're only using FIBONACCI and RSI indicators
            self.active_indicators = {
                'FIBONACCI': True,
                'RSI': True
            }
            
            self.indicator_params = config_json['indicators']
            self.trade_amount = config_json['trading']['amount']
            self.min_volume = config_json['trading']['min_volume']
            self.update_interval = config_json['trading']['update_interval']
            self.max_workers = config_json['trading'].get('max_workers', 10)
            self.kline_interval = str(config_json['trading']['kline']['interval'])
            self.kline_limit = int(config_json['trading']['kline']['limit'])
            
            # Kline interval validation
            valid_intervals = ['1', '3', '5', '15', '30',                    # Minutes
                             '60', '120', '240', '360', '720', '1440']      # Hours
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
            
    def can_place_order(self, symbol, side, qty):
        """Check if order can be placed"""
        try:
            # Get base and quote currency (e.g., for BTCUSDT: base=BTC, quote=USDT)
            base_currency = symbol[:-4] if symbol.endswith('USDT') else symbol.split('USDT')[0]
            quote_currency = "USDT"
            
            # Get order constraints
            min_qty, min_order_amt, decimal_places = self.get_min_order_size(symbol)
            if min_qty is None:
                return False, None
                
            # Get current price
            current_price = float(self.client.get_tickers(category="spot", symbol=symbol)["result"]["list"][0]["lastPrice"])
            
            # Round quantity to appropriate decimal places
            rounded_qty = round(qty, decimal_places)
            
            # Calculate order value
            order_value = rounded_qty * current_price
            
            # Debug output
            self.console.print(f"[cyan]Order check for {symbol}:[/cyan]")
            self.console.print(f"Original qty: {qty}, Rounded qty: {rounded_qty}, Value: {order_value:.2f} USDT")
            self.console.print(f"Min qty: {min_qty}, Min order amt: {min_order_amt}, Decimal places: {decimal_places}")
            
            # Check if quantity meets minimum requirement
            if rounded_qty < min_qty:
                self.console.print(f"[yellow]Quantity ({rounded_qty}) is less than minimum required ({min_qty})[/yellow]")
                return False, None
            
            # Check if we have enough balance for the trade
            wallet = self.get_wallet_balance()
            
            if side == "Buy":
                # For buy orders, check USDT balance
                usdt_needed = order_value
                usdt_balance = wallet.get("USDT", {}).get("free", 0)
                if usdt_balance < usdt_needed:
                    self.console.print(f"[yellow]Insufficient USDT balance. Need: {usdt_needed:.2f} USDT, Have: {usdt_balance:.2f} USDT[/yellow]")
                    return False, None
            else:  # Sell order
                # For sell orders, check if we have the asset
                base_balance = wallet.get(base_currency, {}).get("free", 0)
                if base_balance < rounded_qty:
                    self.console.print(f"[yellow]Insufficient {base_currency} balance. Need: {rounded_qty:.8f}, Have: {base_balance:.8f}[/yellow]")
                    return False, None
                    
            return True, rounded_qty
            
        except Exception as e:
            self.console.print(f"[bold red]Error checking order possibility: {str(e)}[/bold red]")
            return False, None
    
    def get_high_volume_pairs(self):
        """Get USDT pairs with daily volume > minimum volume"""
        self.console.print("[bold blue]Step 1: Identifying high volume markets...[/bold blue]")
        tickers = self.client.get_tickers(category="spot")
        high_volume_pairs = []
        
        for ticker in tickers["result"]["list"]:
            symbol = ticker["symbol"]
            if symbol.endswith("USDT"):
                volume_24h = float(ticker["volume24h"]) * float(ticker["lastPrice"])
                if volume_24h > self.min_volume:
                    high_volume_pairs.append(symbol)
        
        self.console.print(f"[green]Found {len(high_volume_pairs)} markets with >${self.min_volume/1_000_000:.1f}M daily volume[/green]")
        return high_volume_pairs
    
    def analyze_market(self, pair):
        """Analyze a single market"""
        try:
            ticker = self.client.get_tickers(
                category="spot",
                symbol=pair
            )["result"]["list"][0]
            
            current_price = float(ticker["lastPrice"])
            volume = float(ticker["volume24h"]) * current_price / 1_000_000
            
            # Use imported btcusdt_rsi_simple.py functions for RSI calculation
            df = fetch_btcusdt_data(
                client=self.client,
                interval=self.kline_interval,
                limit=self.kline_limit,
                symbol=pair
            )
            
            if df is None or len(df) < 15:  # Need at least enough data for RSI calculation
                self.console.print(f"[yellow]Not enough data for {pair}[/yellow]")
                return pair, None
            
            # Calculate RSI
            rsi_values = calculate_rsi_with_rma(
                df['close'].values, 
                length=self.indicator_params['RSI']['parameters']['period']
            )
            
            # Add RSI to dataframe and filter out initial periods where RSI is not defined
            df['rsi'] = rsi_values
            df_with_rsi = df[df['rsi'] > 0].reset_index(drop=True)
            
            if len(df_with_rsi) == 0:
                self.console.print(f"[yellow]No valid RSI values for {pair}[/yellow]")
                return pair, None
                
            # Get the latest RSI value
            latest_rsi = df_with_rsi['rsi'].iloc[-1]
            rsi_value = round(float(latest_rsi), 2)
            
            # Calculate Fibonacci signals
            high = df['high'].max()
            low = df['low'].min()
            diff = high - low
            levels = self.indicator_params['FIBONACCI']['parameters']['levels']
            fib_levels = {level: low + level * diff for level in levels}
            
            fib_signal = self._get_fibonacci_signal(current_price, fib_levels)
            
            self.console.print(f"[cyan]{pair} - Price: ${current_price:.4f}, RSI: {rsi_value}, Fibonacci: {fib_signal}[/cyan]")
            
            # Create signals dict
            signals = {
                'RSI': rsi_value,
                'FIBONACCI': fib_signal
            }
            
            # Create values dict for display
            values = {
                'RSI': rsi_value
            }
            
            # Add Fibonacci levels to values
            for level in levels:
                values[f'FIB_{int(level*1000)}'] = round(float(fib_levels[level]), 4)
            
            # Store values for potential UI display
            self.last_values = values
            
            market_info = {
                'price': current_price,
                'volume': volume,
                'signals': signals
            }
            
            # *** TRADING LOGIC ***
            # ONLY use FIBONACCI for BUY decisions
            if fib_signal in ['BUY', 'STRONG_BUY']:
                # Check if RSI is not in overbought territory before buying
                overbought = self.indicator_params['RSI']['parameters']['overbought']
                if rsi_value < overbought - 10:
                    # Check if we already have this coin
                    wallet = self.get_wallet_balance()
                    base_currency = pair[:-4] if pair.endswith('USDT') else pair.split('USDT')[0]
                    current_amount = wallet.get(base_currency, {}).get("total", 0)
                    current_value = current_amount * current_price
                    
                    # Only buy if we don't have the coin or its value is less than $1
                    if current_value < 1.0:
                        self.console.print(f"[bold green]Buy Signal detected for {pair} (FIBONACCI: {fib_signal}, RSI: {rsi_value} < {overbought})[/bold green]")
                        self.console.print(f"[green]Current holdings: {current_amount} {base_currency} worth ${current_value:.2f}[/green]")
                        self.place_order(pair, "Buy", 1.0)
                    else:
                        self.console.print(f"[yellow]Buy Signal for {pair}, but already holding {current_amount} {base_currency} worth ${current_value:.2f} (>= $1). Skipping buy.[/yellow]")
                else:
                    self.console.print(f"[yellow]Buy Signal from FIBONACCI: {fib_signal}, but RSI is too high: {rsi_value} >= {overbought}. Skipping buy.[/yellow]")
            
            # ONLY use RSI for SELL decisions
            # If RSI is above overbought threshold (from config)
            overbought = self.indicator_params['RSI']['parameters']['overbought']
            if rsi_value > overbought:
                # Check if we have any of this coin
                wallet = self.get_wallet_balance()
                base_currency = pair[:-4] if pair.endswith('USDT') else pair.split('USDT')[0]
                current_amount = wallet.get(base_currency, {}).get("total", 0)
                
                if current_amount > 0:
                    self.console.print(f"[bold red]Sell Signal detected for {pair} (RSI: {rsi_value}, above {overbought})[/bold red]")
                    self.console.print(f"[red]Selling all {current_amount} {base_currency}[/red]")
                    self.place_order(pair, "Sell", current_amount)  # Sell all available coins
                else:
                    self.console.print(f"[yellow]Sell Signal for {pair}, but no {base_currency} in wallet. Skipping sell.[/yellow]")
                
            return pair, market_info
            
        except Exception as e:
            self.console.print(f"[bold red]Error analyzing {pair}: {str(e)}[/bold red]")
            import traceback
            traceback.print_exc()
            return pair, None
    
    def _get_fibonacci_signal(self, price, fib_levels):
        """Get trading signal based on Fibonacci levels"""
        if price < fib_levels[0.236]:
            return 'STRONG_BUY'
        elif price < fib_levels[0.382]:
            return 'BUY'
        elif price > fib_levels[0.618]:
            return 'SELL'
        return 'NEUTRAL'
    
    def place_order(self, symbol, side, qty):
        """Place a market order"""
        try:
            # Get current price
            current_price = float(self.client.get_tickers(category="spot", symbol=symbol)["result"]["list"][0]["lastPrice"])
            
            # Get minimum order size and decimal places
            min_qty, min_order_amt, decimal_places = self.get_min_order_size(symbol)
            if min_qty is None:
                return None
            
            if side == "Buy":
                # For buy orders, always use fixed amount specified in config
                qty = self.trade_amount / current_price
                qty = 10

                self.console.print(f"[yellow]Placing Buy order for {symbol}:[/yellow]")
                self.console.print(f"Price: {current_price:.8f}")
                self.console.print(f"Quantity: {qty}")
                
            else:  # Sell order
                # For sell orders, use the available balance
                wallet = self.get_wallet_balance()
                base_currency = symbol[:-4] if symbol.endswith('USDT') else symbol.split('USDT')[0]
                qty = wallet.get(base_currency, {}).get("free", 0)
                
                # Round to correct decimal places
                if decimal_places is not None:
                    import math
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
                
                self.console.print(f"[yellow]Placing Sell order for {symbol}:[/yellow]")
                self.console.print(f"Price: {current_price:.8f}")
                self.console.print(f"Quantity: {qty}")
                self.console.print(f"Total Value: {value:.2f} USDT")
            
            # Check if we can place the order
            can_place, rounded_qty = self.can_place_order(symbol, side, qty)
            if not can_place:
                self.console.print(f"[yellow]Cannot place {side} order for {symbol}[/yellow]")
                return None
                
            # Place the order
            order = self.client.place_order(
                category="spot",
                symbol=symbol,
                side=side,
                orderType="MARKET",
                qty=str(rounded_qty)
            )
            
            self.console.print(f"[bold green]✓ {side} order placed for {rounded_qty} {symbol} at market price[/bold green]")
            return order
            
        except Exception as e:
            self.console.print(f"[bold red]Error placing order: {str(e)}[/bold red]")
            return None
    
    def display_market_data(self, market_data):
        """Display market data in a rich table"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        table = Table(title=f"Market Analysis - {now}")
        
        # Add columns
        table.add_column("Symbol", style="cyan")
        table.add_column("Price", justify="right", style="green")
        table.add_column("24h Volume", justify="right", style="yellow")
        table.add_column("FIBONACCI", justify="center")
        table.add_column("RSI", justify="right")  # Changed to right-aligned for numeric display
        
        # Add rows
        for symbol, data in market_data.items():
            if data is None:
                continue
            
            fib_signal = data['signals']['FIBONACCI']
            rsi_value = data['signals']['RSI']
            
            # Determine color for Fibonacci
            fib_color = "green" if fib_signal in ['BUY', 'STRONG_BUY'] else "red" if fib_signal == 'SELL' else "white"
            
            # Determine color for RSI
            rsi_params = self.indicator_params['RSI']['parameters']
            rsi_color = "green" if rsi_value < rsi_params['oversold'] else "red" if rsi_value > rsi_params['overbought'] else "white"
            
            table.add_row(
                symbol,
                f"${data['price']:.4f}",
                f"${data['volume']:.2f}M",
                f"[{fib_color}]{fib_signal}[/{fib_color}]",
                f"[{rsi_color}]{rsi_value}[/{rsi_color}]"  # Display actual RSI value
            )
        
        self.console.print(table)
    
    def run(self):
        """Main bot loop"""
        self.console.print(Panel.fit("[bold green]Trading Bot Started[/bold green]", title="Status"))
        
        while True:
            try:
                # Step 1: Get markets
                self.console.print("[bold blue]Step 1: Identifying high volume markets...[/bold blue]")
                pairs = self.get_high_volume_pairs()
                market_data = {}
                
                # Step 2: Analyze markets in parallel
                self.console.print("[bold blue]Step 2: Analyzing markets in parallel...[/bold blue]")
                
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    transient=True
                ) as progress:
                    task = progress.add_task("Analyzing markets...", total=len(pairs))
                    
                    with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                        future_to_pair = {executor.submit(self.analyze_market, pair): pair for pair in pairs}
                        
                        for future in as_completed(future_to_pair):
                            pair, result = future.result()
                            if result is not None:
                                market_data[pair] = result
                            progress.advance(task)
                
                # Display results
                self.console.print("\n[bold blue]Market Analysis Results:[/bold blue]")
                self.display_market_data(market_data)
                
                # Wait before next iteration
                self.console.print(f"[yellow]Waiting {self.update_interval} seconds before next analysis...[/yellow]")
                time.sleep(self.update_interval)
                
            except Exception as e:
                self.console.print(f"[bold red]Error: {str(e)}[/bold red]")
                time.sleep(5)

if __name__ == "__main__":
    load_dotenv()
    
    import argparse
    
    parser = argparse.ArgumentParser(description='Trading Bot')
    parser.add_argument('--testnet', action='store_true', help='Use testnet instead of mainnet')
    parser.add_argument('--config', type=str, default='config.json', help='Path to configuration file')
    
    args = parser.parse_args()
    
    api_key = os.getenv('BYBIT_API_KEY')
    api_secret = os.getenv('BYBIT_API_SECRET')
    
    if not api_key or not api_secret:
        print("Please set BYBIT_API_KEY and BYBIT_API_SECRET in .env file")
        exit(1)
    
    console = Console()
    console.print(f"[bold blue]Starting Trading Bot...[/bold blue]")
    console.print(f"[cyan]Using {'testnet' if args.testnet else 'mainnet'}[/cyan]")
    
    bot = TradingBot(api_key, api_secret, config_path=args.config, testnet=args.testnet)
    bot.run() 
