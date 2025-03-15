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
import math

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
        self.positions = self.get_positions()
        
    def load_config(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                
            self.active_indicators = {
                name: indicator['enabled']
                for name, indicator in config['indicators'].items()
            }
            self.indicator_params = config['indicators']
            self.trade_amount = config['trading']['amount']
            self.min_volume = config['trading']['min_volume']
            self.update_interval = config['trading']['update_interval']
            self.max_workers = config['trading'].get('max_workers', 10)
            self.stop_loss_percentage = config['trading'].get('stop_loss_percentage', 2)
            self.take_profit_percentage = config['trading'].get('take_profit_percentage', 3)
            self.kline_interval = str(config['trading']['kline']['interval'])
            self.kline_limit = int(config['trading']['kline']['limit'])
            
            # Kline interval doğrulama
            valid_intervals = ['1', '3', '5', '15', '30',                    # Dakikalar
                             '60', '120', '240', '360', '720', '1440']      # Saatler
            if self.kline_interval not in valid_intervals:
                raise ValueError(f"Geçersiz kline aralığı. Geçerli değerler: {', '.join(valid_intervals)}")
            
            self.console.print(f"[green]Configuration loaded successfully from {config_path}[/green]")
            self.console.print(f"[blue]Using {self.kline_interval} minute candles, fetching last {self.kline_limit} candles[/blue]")
            self.console.print(f"[blue]Stop Loss: {self.stop_loss_percentage}%, Take Profit: {self.take_profit_percentage}%[/blue]")
        except Exception as e:
            self.console.print(f"[bold red]Error loading config: {str(e)}[/bold red]")
            raise
        
    def get_trade_history(self, symbol):
        """Get trade history for a symbol to find entry price"""
        try:
            # Get last 50 orders
            orders = self.client.get_order_history(
                category="spot",
                symbol=symbol,
                limit=50,
                status="Filled"
            )
            
            if orders and orders.get("retCode") == 0 and orders.get("result", {}).get("list"):
                # Filter buy orders only
                buy_orders = [order for order in orders["result"]["list"] 
                            if order["side"] == "Buy"]
                if buy_orders:
                    # Get the most recent buy order
                    last_buy = buy_orders[0]
                    return {
                        "entry_price": float(last_buy["avgPrice"]),
                        "qty": float(last_buy["qty"])
                    }
            return None
            
        except Exception as e:
            self.console.print(f"[yellow]Warning: Could not get order history for {symbol}: {str(e)}[/yellow]")
            return None

    def get_positions(self):
        """Get current positions and set entry prices from trade history"""
        try:
            balances = self.client.get_wallet_balance(accountType="UNIFIED")
            positions = {}
            
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
                            # Get current price for the coin if it's not USDT
                            entry_price = None
                            stop_loss = None
                            take_profit = None
                            
                            if coin["coin"] != "USDT":
                                symbol = f"{coin['coin']}USDT"
                                try:
                                    # Get current price
                                    ticker = self.client.get_tickers(category="spot", symbol=symbol)["result"]["list"][0]
                                    current_price = float(ticker["lastPrice"])
                                    
                                    # Get trade history to find entry price
                                    trade_info = self.get_trade_history(symbol)
                                    if trade_info:
                                        entry_price = trade_info["entry_price"]
                                        self.console.print(f"[green]Found entry price for {coin['coin']} from trade history: {entry_price}[/green]")
                                    else:
                                        # If no trade history found, use current price
                                        entry_price = current_price
                                        self.console.print(f"[yellow]No trade history found for {coin['coin']}, using current price: {current_price}[/yellow]")
                                    
                                    # Calculate stop loss and take profit
                                    stop_loss = entry_price * (1 - self.stop_loss_percentage / 100)
                                    take_profit = entry_price * (1 + self.take_profit_percentage / 100)
                                        
                                except Exception as e:
                                    self.console.print(f"[yellow]Warning: Could not get price for {symbol}: {str(e)}[/yellow]")
                                    continue
                            
                            positions[coin["coin"]] = {
                                "free": wallet_balance - float(coin.get("locked", 0)),
                                "locked": float(coin.get("locked", 0)),
                                "total": wallet_balance,
                                "entry_price": entry_price,
                                "stop_loss": stop_loss,
                                "take_profit": take_profit
                            }
                            self.console.print(f"[green]Added position for {coin['coin']}: {positions[coin['coin']]}[/green]")
                    except (KeyError, ValueError) as e:
                        self.console.print(f"[yellow]Warning: Could not process balance for coin: {coin.get('coin', 'UNKNOWN')} - Error: {str(e)}[/yellow]")
                        continue
            
            # Save all positions immediately
            self.positions = positions
            self.save_positions_to_file()
            return positions
            
        except Exception as e:
            self.console.print(f"[bold red]Error getting positions: {str(e)}[/bold red]")
            return {}
            
    def get_min_order_size(self, symbol):
        """Get minimum order size for a symbol"""
        try:
            # Get instrument info
            instrument_info = self.client.get_instruments_info(
                category="spot",
                symbol=symbol
            )
            
            # Debug: Print raw API response
            self.console.print(f"[cyan]Raw API Response for {symbol}:[/cyan]")
            self.console.print(json.dumps(instrument_info, indent=2))
            
            # Extract values from response
            lot_size_filter = instrument_info["result"]["list"][0]["lotSizeFilter"]
            min_qty = float(lot_size_filter["minOrderQty"])
            min_order_amt = float(lot_size_filter["minOrderAmt"])
            
            # Calculate decimal places from basePrecision
            base_precision = lot_size_filter["basePrecision"]
            decimal_places = len(base_precision.split(".")[1]) if "." in base_precision else 0
            
            # Debug: Print trading constraints
            self.console.print(f"[cyan]Trading constraints for {symbol}:[/cyan]")
            self.console.print(f"Minimum quantity: {min_qty}")
            self.console.print(f"Minimum order amount: {min_order_amt}")
            self.console.print(f"Decimal places: {decimal_places}")
            
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
            if side == "Buy":
                # Check if we already have this coin
                current_position = self.positions.get(base_currency, {}).get("total", 0)
                if current_position > 0:
                    self.console.print(f"[yellow]Already holding {current_position:.8f} {base_currency}, skipping buy order[/yellow]")
                    return False, None
                
                # For buy orders, check USDT balance
                usdt_needed = order_value
                usdt_balance = self.positions.get("USDT", {}).get("free", 0)
                if usdt_balance < usdt_needed:
                    self.console.print(f"[yellow]Insufficient USDT balance. Need: {usdt_needed:.2f} USDT, Have: {usdt_balance:.2f} USDT[/yellow]")
                    return False, None
            else:  # Sell order
                # For sell orders, check if we have the asset
                base_balance = self.positions.get(base_currency, {}).get("free", 0)
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
            # Get market data
            df = self.get_klines(pair)
            signals = self.calculate_indicators(df)
            
            ticker = self.client.get_tickers(
                category="spot",
                symbol=pair
            )["result"]["list"][0]
            
            market_info = {
                'price': float(ticker["lastPrice"]),
                'volume': float(ticker["volume24h"]) * float(ticker["lastPrice"]) / 1_000_000,
                'signals': signals
            }
            
            # Check signals and place orders
            active_signals = {k: v for k, v in signals.items() if self.active_indicators[k]}
            if active_signals and all(signal in ['BUY', 'STRONG_BUY'] for signal in active_signals.values()):
                self.console.print(f"[bold green]Buy Signal detected for {pair}[/bold green]")
                # Just pass 1.0 as a placeholder, place_order will calculate the correct quantity
                self.place_order(pair, "Buy", 1.0)
            elif active_signals and all(signal == 'SELL' for signal in active_signals.values()):
                self.console.print(f"[bold red]Sell Signal detected for {pair}[/bold red]")
                # Just pass 1.0 as a placeholder, place_order will calculate the correct quantity
                self.place_order(pair, "Sell", 1.0)
                
            return pair, market_info
            
        except Exception as e:
            self.console.print(f"[bold red]Error analyzing {pair}: {str(e)}[/bold red]")
            return pair, None
    
    def get_klines(self, symbol):
        """Get historical klines/candlestick data"""
        klines = self.client.get_kline(
            category="spot",
            symbol=symbol,
            interval=self.kline_interval,
            limit=self.kline_limit
        )
        
        df = pd.DataFrame(klines["result"]["list"], 
                         columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        return df
    
    def calculate_indicators(self, df):
        """Calculate technical indicators"""
        signals = {}
        
        if self.active_indicators['RSI']:
            params = self.indicator_params['RSI']['parameters']
            rsi = RSIIndicator(df['close'], window=params['period']).rsi()
            signals['RSI'] = 'BUY' if rsi.iloc[-1] < params['oversold'] else 'SELL' if rsi.iloc[-1] > params['overbought'] else 'NEUTRAL'
            
        if self.active_indicators['SMA']:
            params = self.indicator_params['SMA']['parameters']
            sma_short = SMAIndicator(df['close'], window=params['short_period']).sma_indicator()
            sma_long = SMAIndicator(df['close'], window=params['long_period']).sma_indicator()
            signals['SMA'] = 'BUY' if sma_short.iloc[-1] > sma_long.iloc[-1] else 'SELL'
            
        if self.active_indicators['MACD']:
            params = self.indicator_params['MACD']['parameters']
            macd = MACD(
                df['close'],
                window_fast=params['fast_period'],
                window_slow=params['slow_period'],
                window_sign=params['signal_period']
            )
            signals['MACD'] = 'BUY' if macd.macd().iloc[-1] > macd.macd_signal().iloc[-1] else 'SELL'
            
        if self.active_indicators['BBANDS']:
            params = self.indicator_params['BBANDS']['parameters']
            bb = BollingerBands(df['close'], window=params['period'], window_dev=params['std_dev'])
            current_price = df['close'].iloc[-1]
            signals['BBANDS'] = 'BUY' if current_price < bb.bollinger_lband().iloc[-1] else 'SELL' if current_price > bb.bollinger_hband().iloc[-1] else 'NEUTRAL'
            
        if self.active_indicators['FIBONACCI']:
            high = df['high'].max()
            low = df['low'].min()
            diff = high - low
            levels = self.indicator_params['FIBONACCI']['parameters']['levels']
            fib_levels = {level: low + level * diff for level in levels}
            current_price = df['close'].iloc[-1]
            signals['FIBONACCI'] = self._get_fibonacci_signal(current_price, fib_levels)
            
        return signals
    
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
                # Check if we already have this coin
                base_currency = symbol[:-4] if symbol.endswith('USDT') else symbol.split('USDT')[0]
                current_qty = self.positions.get(base_currency, {}).get("free", 0)
                current_value = current_qty * current_price
                
                if current_value > 2:
                    self.console.print(f"[yellow]Already holding {current_qty} {base_currency} worth {current_value:.2f} USDT (>2 USDT), skipping buy[/yellow]")
                    return None
                
                # For buy orders, always use 5.5
                qty = 5.5
                
                self.console.print(f"[yellow]Placing Buy order for {symbol}:[/yellow]")
                self.console.print(f"Price: {current_price:.8f}")
                self.console.print(f"Quantity: {qty}")
                
            else:  # Sell order
                # For sell orders, use the available balance
                base_currency = symbol[:-4] if symbol.endswith('USDT') else symbol.split('USDT')[0]
                qty = self.positions.get(base_currency, {}).get("free", 0)
                
                # Round to correct decimal places
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
            
            # Place the order
            order = self.client.place_order(
                category="spot",
                symbol=symbol,
                side=side,
                orderType="MARKET",
                qty=str(qty)
            )
            
            self.console.print(f"[bold green]✓ {side} order placed for {qty} {symbol} at market price[/bold green]")
            
            # Update positions after order
            self.positions = self.get_positions()
            
            # If it's a buy order, save the position data
            if side == "Buy":
                base_currency = symbol[:-4] if symbol.endswith('USDT') else symbol.split('USDT')[0]
                current_position = self.positions.get(base_currency, {})
                current_total = float(current_position.get("total", 0))
                
                # Add new quantity to total
                new_total = current_total + float(qty)
                
                self.positions[base_currency] = {
                    "total": new_total,
                    "entry_price": current_price,
                    "stop_loss": current_price * (1 - self.stop_loss_percentage / 100),
                    "take_profit": current_price * (1 + self.take_profit_percentage / 100)
                }
                
                self.console.print(f"[green]Updated position for {base_currency}:[/green]")
                self.console.print(f"Previous total: {current_total}")
                self.console.print(f"New purchase: {qty}")
                self.console.print(f"New total: {new_total}")
                
                self.save_positions_to_file()
            elif side == "Sell":
                # If it's a sell order, remove the position data for this coin
                base_currency = symbol[:-4] if symbol.endswith('USDT') else symbol.split('USDT')[0]
                if base_currency in self.positions:
                    del self.positions[base_currency]
                    self.save_positions_to_file()
            
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
        for indicator in self.active_indicators:
            if self.active_indicators[indicator]:
                table.add_column(indicator, justify="center")
        
        # Add rows
        for symbol, data in market_data.items():
            if data is None:
                continue
            row = [
                symbol,
                f"${data['price']:.4f}",
                f"${data['volume']:.2f}M"
            ]
            for indicator in self.active_indicators:
                if self.active_indicators[indicator]:
                    signal = data['signals'][indicator]
                    color = "green" if signal in ['BUY', 'STRONG_BUY'] else "red" if signal == 'SELL' else "white"
                    row.append(f"[{color}]{signal}[/{color}]")
            table.add_row(*row)
        
        self.console.print(table)
    
    def run(self):
        """Main bot loop"""
        self.console.print(Panel.fit("[bold green]Trading Bot Started[/bold green]", title="Status"))
        
        while True:
            try:
                # Step 1: Check existing positions for stop loss and take profit
                self.console.print("[bold blue]Step 1: Checking existing positions...[/bold blue]")
                self.check_positions()
                
                # Step 2: Get markets
                self.console.print("[bold blue]Step 2: Identifying high volume markets...[/bold blue]")
                pairs = self.get_high_volume_pairs()
                market_data = {}
                
                # Step 3: Analyze markets in parallel
                self.console.print("[bold blue]Step 3: Analyzing markets in parallel...[/bold blue]")
                
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

    def check_positions(self):
        """Check current positions for stop loss and take profit levels"""
        try:
            # Update positions first
            self.positions = self.get_positions()
            
            for coin, position in self.positions.items():
                # Skip USDT
                if coin == "USDT":
                    continue
                    
                # Skip if no entry price (shouldn't happen, but just in case)
                if not position.get("entry_price"):
                    continue
                    
                symbol = f"{coin}USDT"
                
                try:
                    # Get current price
                    ticker = self.client.get_tickers(category="spot", symbol=symbol)["result"]["list"][0]
                    current_price = float(ticker["lastPrice"])
                    
                    # Calculate price change percentage
                    price_change = ((current_price - position["entry_price"]) / position["entry_price"]) * 100
                    
                    self.console.print(f"[cyan]Checking {symbol}:[/cyan]")
                    self.console.print(f"Entry: {position['entry_price']:.8f}, Current: {current_price:.8f}, Change: {price_change:.2f}%")
                    self.console.print(f"Stop Loss: {position['stop_loss']:.8f}, Take Profit: {position['take_profit']:.8f}")
                    
                    # Check stop loss
                    if current_price <= position["stop_loss"]:
                        self.console.print(f"[red]Stop Loss triggered for {symbol} at {current_price:.8f}[/red]")
                        self.place_order(symbol, "Sell", position["total"])
                        
                    # Check take profit
                    elif current_price >= position["take_profit"]:
                        self.console.print(f"[green]Take Profit triggered for {symbol} at {current_price:.8f}[/green]")
                        self.place_order(symbol, "Sell", position["total"])
                        
                except Exception as e:
                    self.console.print(f"[yellow]Warning: Could not check {symbol}: {str(e)}[/yellow]")
                    continue
                    
        except Exception as e:
            self.console.print(f"[bold red]Error checking positions: {str(e)}[/bold red]")

    def save_positions_to_file(self):
        """Save positions data to a JSON file"""
        try:
            # Only save non-USDT positions with their trading data
            positions_to_save = {}
            for coin, position in self.positions.items():
                if coin != "USDT" and position.get("entry_price"):
                    positions_to_save[coin] = {
                        "entry_price": position["entry_price"],
                        "stop_loss": position["stop_loss"],
                        "take_profit": position["take_profit"],
                        "total": position["total"]
                    }
            
            # Save to file
            with open('positions.json', 'w') as f:
                json.dump(positions_to_save, f, indent=4)
            
            self.console.print("[green]Positions saved to positions.json[/green]")
            
        except Exception as e:
            self.console.print(f"[bold red]Error saving positions: {str(e)}[/bold red]")
            
    def load_positions_from_file(self):
        """Load positions data from JSON file"""
        try:
            if os.path.exists('positions.json'):
                with open('positions.json', 'r') as f:
                    saved_positions = json.load(f)
                
                self.console.print("[green]Loaded saved positions:[/green]")
                for coin, data in saved_positions.items():
                    self.console.print(f"[cyan]{coin}: Entry: {data['entry_price']}, SL: {data['stop_loss']}, TP: {data['take_profit']}[/cyan]")
                
                return saved_positions
            return {}
            
        except Exception as e:
            self.console.print(f"[bold red]Error loading positions: {str(e)}[/bold red]")
            return {}

if __name__ == "__main__":
    load_dotenv()
    
    api_key = os.getenv('BYBIT_API_KEY')
    api_secret = os.getenv('BYBIT_API_SECRET')
    
    if not api_key or not api_secret:
        print("Please set BYBIT_API_KEY and BYBIT_API_SECRET in .env file")
        exit(1)
    
    bot = TradingBot(api_key, api_secret, config_path='config.json', testnet=False)
    bot.run() 