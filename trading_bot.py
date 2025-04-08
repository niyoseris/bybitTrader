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
import logging
import config
from data_collector import fetch_klines

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
        
        # Pozisyon kontrollerini yapılandırmadan kontrol et
        if not self.enable_position_checks:
            self.console.print("[yellow]Position checks are disabled, skipping position queries[/yellow]")
            self.positions = {}  # Boş bir positions sözlüğü oluştur
        else:
            # First get current positions from the exchange
            self.positions = self.get_positions()
            
            # Then load saved positions from file and merge them
            saved_positions = self.load_positions_from_file()
            if saved_positions:
                # Update positions with saved data for coins not currently in the wallet
                for coin, data in saved_positions.items():
                    if coin not in self.positions:
                        self.console.print(f"[cyan]Adding saved position for {coin} from positions.json[/cyan]")
                        # Create a basic position structure
                        self.positions[coin] = {
                            "free": data.get("total", 0),
                            "locked": 0,
                            "total": data.get("total", 0),
                            "entry_price": data.get("entry_price"),
                            "stop_loss": data.get("stop_loss"),
                            "take_profit": data.get("take_profit")
                        }
                    else:
                        # Update existing position with saved trading data if missing
                        if not self.positions[coin].get("entry_price") and data.get("entry_price"):
                            self.console.print(f"[cyan]Updating position data for {coin} from positions.json[/cyan]")
                            self.positions[coin]["entry_price"] = data.get("entry_price")
                            self.positions[coin]["stop_loss"] = data.get("stop_loss")
                            self.positions[coin]["take_profit"] = data.get("take_profit")
                
                # Save the merged positions
                self.save_positions_to_file()
        
    def load_config(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                config_json = json.load(f)
                
            self.active_indicators = {
                name: (indicator.get('enabled_for_buy', False) or indicator.get('enabled_for_sell', False))
                for name, indicator in config_json['indicators'].items()
            }
            
            self.buy_indicators = {
                name: indicator.get('enabled_for_buy', False)
                for name, indicator in config_json['indicators'].items()
            }
            
            self.sell_indicators = {
                name: indicator.get('enabled_for_sell', False)
                for name, indicator in config_json['indicators'].items()
            }
            
            self.indicator_params = config_json['indicators']
            self.trade_amount = config_json['trading']['amount']
            self.min_volume = config_json['trading']['min_volume']
            self.update_interval = config_json['trading']['update_interval']
            self.max_workers = config_json['trading'].get('max_workers', 10)
            
            # Get stop loss and take profit from config.py instead of JSON config
            self.stop_loss_percentage = config.STOP_LOSS_PERCENT * 100  # Convert to percentage
            self.take_profit_percentage = config.TAKE_PROFIT_PERCENT * 100  # Convert to percentage
            
            # Position management ayarlarını yükle - varsayılan olarak hepsini etkinleştir
            position_management = config_json['trading'].get('position_management', {})
            self.enable_position_checks = position_management.get('enable_position_checks', True)
            self.enable_take_profit = position_management.get('enable_take_profit', True)
            self.enable_stop_loss = position_management.get('enable_stop_loss', True)
            
            self.kline_interval = str(config_json['trading']['kline']['interval'])
            self.kline_limit = int(config_json['trading']['kline']['limit'])
            
            # Kline interval doğrulama
            valid_intervals = ['1', '3', '5', '15', '30',                    # Dakikalar
                             '60', '120', '240', '360', '720', '1440']      # Saatler
            if self.kline_interval not in valid_intervals:
                raise ValueError(f"Geçersiz kline aralığı. Geçerli değerler: {', '.join(valid_intervals)}")
            
            self.console.print(f"[green]Configuration loaded successfully from {config_path}[/green]")
            self.console.print(f"[blue]Using {self.kline_interval} minute candles, fetching last {self.kline_limit} candles[/blue]")
            
            self.console.print("[blue]Active indicators for BUY signals:[/blue]")
            for indicator, enabled in self.buy_indicators.items():
                if enabled:
                    self.console.print(f"  - {indicator}")
            
            self.console.print("[blue]Active indicators for SELL signals:[/blue]")
            for indicator, enabled in self.sell_indicators.items():
                if enabled:
                    self.console.print(f"  - {indicator}")
                    
            self.console.print(f"[blue]Stop Loss: {self.stop_loss_percentage}%, Take Profit: {self.take_profit_percentage}%[/blue]")
            
            # Position management durumunu yazdır
            self.console.print("[blue]Position management settings:[/blue]")
            self.console.print(f"  - Position checks: {'Enabled' if self.enable_position_checks else 'Disabled'}")
            self.console.print(f"  - Take profit: {'Enabled' if self.enable_take_profit else 'Disabled'}")
            self.console.print(f"  - Stop loss: {'Enabled' if self.enable_stop_loss else 'Disabled'}")
            
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
            self.console.print("[cyan]Fetching current positions from Bybit...[/cyan]")
            balances = self.client.get_wallet_balance(accountType="UNIFIED")
            positions = {}
            
            # Debug: Log the raw balance response
            self.console.print(f"[cyan]API Response:[/cyan] {balances.get('retCode')} - {balances.get('retMsg')}")
            
            if balances.get("retCode") != 0:
                self.console.print(f"[bold red]API Error: {balances.get('retMsg')}[/bold red]")
                return {}
            
            if not balances.get("result") or not balances["result"].get("list"):
                self.console.print("[yellow]No balance data received from API[/yellow]")
                return {}
                
            # Debug: Log the number of accounts found
            self.console.print(f"[cyan]Found {len(balances['result']['list'])} account(s)[/cyan]")
            
            for account in balances["result"]["list"]:
                if "coin" not in account:
                    self.console.print(f"[yellow]Warning: Account has no 'coin' field: {account}[/yellow]")
                    continue
                    
                # Debug: Log the number of coins found in this account
                self.console.print(f"[cyan]Found {len(account['coin'])} coin(s) in account[/cyan]")
                
                for coin in account["coin"]:
                    try:
                        wallet_balance = float(coin.get("walletBalance", 0))
                        if wallet_balance > 0:
                            self.console.print(f"[green]Found {wallet_balance} {coin['coin']}[/green]")
                            
                            # Get current price for the coin if it's not USDT
                            entry_price = None
                            stop_loss = None
                            take_profit = None
                            
                            if coin["coin"] != "USDT":
                                symbol = f"{coin['coin']}USDT"
                                try:
                                    # Get current price
                                    ticker_response = self.client.get_tickers(category="spot", symbol=symbol)
                                    
                                    # Debug: Log the ticker response
                                    if ticker_response.get("retCode") != 0:
                                        self.console.print(f"[yellow]Warning: Could not get ticker for {symbol}: {ticker_response.get('retMsg')}[/yellow]")
                                        continue
                                        
                                    if not ticker_response.get("result") or not ticker_response["result"].get("list"):
                                        self.console.print(f"[yellow]Warning: No ticker data for {symbol}[/yellow]")
                                        continue
                                    
                                    ticker = ticker_response["result"]["list"][0]
                                    current_price = float(ticker["lastPrice"])
                                    self.console.print(f"[green]Current price for {symbol}: {current_price}[/green]")
                                    
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
            
            # Summary of positions
            if positions:
                self.console.print(f"[bold green]Found {len(positions)} coins with non-zero balances[/bold green]")
            else:
                self.console.print("[yellow]No positions with non-zero balances found[/yellow]")
            
            # Save all positions immediately
            self.positions = positions
            self.save_positions_to_file()
            return positions
            
        except Exception as e:
            self.console.print(f"[bold red]Error getting positions: {str(e)}[/bold red]")
            self.console.print(f"[red]Error type: {type(e).__name__}[/red]")
            import traceback
            traceback.print_exc()
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
                
                # Check if we have any open orders for this symbol
                has_open_orders = self.has_open_orders(symbol)
                
                if current_position > 0 or has_open_orders:
                    self.console.print(f"[yellow]Already holding {current_position:.8f} {base_currency} or has open orders, skipping buy order[/yellow]")
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
            signals, values = self.calculate_indicators(df)
            
            ticker = self.client.get_tickers(
                category="spot",
                symbol=pair
            )["result"]["list"][0]
            
            # BUY sinyalleri için sadece enabled_for_buy olan indikatörleri kontrol et
            buy_signals = []
            for indicator_name, is_enabled in self.buy_indicators.items():
                if is_enabled and indicator_name in signals:
                    if signals[indicator_name] in ['BUY', 'STRONG_BUY']:
                        buy_signals.append(indicator_name)
            
            # SELL sinyalleri için sadece enabled_for_sell olan indikatörleri kontrol et
            sell_signals = []
            for indicator_name, is_enabled in self.sell_indicators.items():
                if is_enabled and indicator_name in signals:
                    if signals[indicator_name] == 'SELL':
                        sell_signals.append(indicator_name)
            
            # Nötr sinyaller
            neutral_signals = [k for k, v in signals.items() if v == 'NEUTRAL']
            
            market_info = {
                'price': float(ticker["lastPrice"]),
                'volume': float(ticker["volume24h"]) * float(ticker["lastPrice"]) / 1_000_000,
                'signals': signals,
                'values': values,
                'buy_indicators': buy_signals,
                'sell_indicators': sell_signals,
                'neutral_indicators': neutral_signals
            }
            
            # Alış ve satış için farklı indikatörleri kontrol et
            active_buy_indicators = [ind for ind, enabled in self.buy_indicators.items() if enabled]
            active_sell_indicators = [ind for ind, enabled in self.sell_indicators.items() if enabled]
            
            # Öncelikle RSI değerini kontrol et - eğer RSI 60'ın üzerindeyse hemen satış yap
            if self.active_indicators['RSI'] and 'RSI' in values:
                rsi_value = values['RSI']
                base_currency = pair[:-4] if pair.endswith('USDT') else pair.split('USDT')[0]
                current_qty = self.positions.get(base_currency, {}).get("free", 0)
                
                # RSI 60'ın üzerindeyse ve elimizde bu coin varsa, hemen satış yap
                if rsi_value > 60 and current_qty > 0:
                    self.console.print(f"[bold red]DIRECT SELL! RSI value {rsi_value} > 60 for {pair}[/bold red]")
                    self.place_order(pair, "Sell", 1.0)
                    market_info['combined_signal'] = 'SELL'
                    return pair, market_info
            
            # RSI satış durumu değilse, normal alış/satış mantığını uygula
            
            # Alış sinyalleri: enabled_for_buy olan TÜM indikatörler alış sinyali veriyorsa (AND mantığı)
            all_buy_signals = True
            for indicator in active_buy_indicators:
                if indicator in signals and signals[indicator] not in ['BUY', 'STRONG_BUY']:
                    all_buy_signals = False
                    break
            
            # Satış sinyalleri: enabled_for_sell olan HERHANGİ BİR indikatör satış sinyali veriyorsa (OR mantığı)
            any_sell_signals = False
            for indicator in active_sell_indicators:
                if indicator in signals and signals[indicator] == 'SELL':
                    any_sell_signals = True
                    break
            
            # Combined signal oluştur
            if all_buy_signals and active_buy_indicators:
                market_info['combined_signal'] = 'BUY'
                
                # Hali hazırda bu coini alıp almadığımızı kontrol et
                base_currency = pair[:-4] if pair.endswith('USDT') else pair.split('USDT')[0]
                current_qty = self.positions.get(base_currency, {}).get("free", 0)
                current_price = float(ticker["lastPrice"])
                current_value = current_qty * current_price
                
                if current_value > 1:  # 2 dolar yerine 1 dolar olarak değiştirildi
                    self.console.print(f"[bold yellow]Buy Signal detected for {pair} but already holding {current_qty} {base_currency} worth {current_value:.2f} USDT (>1 USDT), skipping buy[/bold yellow]")
                else:
                    self.console.print(f"[bold green]Buy Signal detected for {pair} (All buy indicators: {', '.join(active_buy_indicators)})[/bold green]")
                    # Just pass 1.0, place_order will calculate the correct quantity
                    self.place_order(pair, "Buy", 1.0)
                    
            elif any_sell_signals and active_sell_indicators:  # OR mantığı burada kullanılıyor
                market_info['combined_signal'] = 'SELL'
                
                # Hali hazırda bu coini elimizde var mı kontrol et (sadece varsa satışa geç)
                base_currency = pair[:-4] if pair.endswith('USDT') else pair.split('USDT')[0]
                current_qty = self.positions.get(base_currency, {}).get("free", 0)
                
                if current_qty > 0:
                    active_sell_signals = [ind for ind in active_sell_indicators if ind in signals and signals[ind] == 'SELL']
                    self.console.print(f"[bold red]Sell Signal detected for {pair} (Selling indicators: {', '.join(active_sell_signals)})[/bold red]")
                    # Just pass 1.0, place_order will calculate the correct quantity
                    self.place_order(pair, "Sell", 1.0)
                else:
                    self.console.print(f"[yellow]Sell Signal detected for {pair} but no position to sell[/yellow]")
            else:
                market_info['combined_signal'] = 'NEUTRAL'
                
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
        values = {}  # Dictionary to store actual indicator values
        
        if self.active_indicators['RSI']:
            params = self.indicator_params['RSI']['parameters']
            rsi = RSIIndicator(df['close'], window=params['period']).rsi()
            rsi_value = rsi.iloc[-1]
            signals['RSI'] = 'BUY' if rsi_value < params['oversold'] else 'SELL' if rsi_value > params['overbought'] else 'NEUTRAL'
            values['RSI'] = round(float(rsi_value), 2)
            
        if self.active_indicators['SMA']:
            params = self.indicator_params['SMA']['parameters']
            sma_short = SMAIndicator(df['close'], window=params['short_period']).sma_indicator()
            sma_long = SMAIndicator(df['close'], window=params['long_period']).sma_indicator()
            sma_short_value = sma_short.iloc[-1]
            sma_long_value = sma_long.iloc[-1]
            signals['SMA'] = 'BUY' if sma_short_value > sma_long_value else 'SELL'
            values['SMA_Short'] = round(float(sma_short_value), 4)
            values['SMA_Long'] = round(float(sma_long_value), 4)
            values['SMA_Diff'] = round(float(sma_short_value - sma_long_value), 4)
            
        if self.active_indicators['MACD']:
            params = self.indicator_params['MACD']['parameters']
            macd_obj = MACD(
                df['close'],
                window_fast=params['fast_period'],
                window_slow=params['slow_period'],
                window_sign=params['signal_period']
            )
            macd_line = macd_obj.macd().iloc[-1]
            macd_signal = macd_obj.macd_signal().iloc[-1]
            macd_hist = macd_obj.macd_diff().iloc[-1]
            
            signals['MACD'] = 'BUY' if macd_line > macd_signal else 'SELL'
            values['MACD_Line'] = round(float(macd_line), 4)
            values['MACD_Signal'] = round(float(macd_signal), 4)
            values['MACD_Hist'] = round(float(macd_hist), 4)
            
        if self.active_indicators['BBANDS']:
            params = self.indicator_params['BBANDS']['parameters']
            bb = BollingerBands(df['close'], window=params['period'], window_dev=params['std_dev'])
            current_price = df['close'].iloc[-1]
            bb_upper = bb.bollinger_hband().iloc[-1]
            bb_lower = bb.bollinger_lband().iloc[-1]
            bb_middle = bb.bollinger_mavg().iloc[-1]
            
            signals['BBANDS'] = 'BUY' if current_price < bb_lower else 'SELL' if current_price > bb_upper else 'NEUTRAL'
            values['BB_Upper'] = round(float(bb_upper), 4)
            values['BB_Middle'] = round(float(bb_middle), 4)
            values['BB_Lower'] = round(float(bb_lower), 4)
            values['BB_Width'] = round(float((bb_upper - bb_lower) / bb_middle * 100), 2)  # % Band width
            
        if self.active_indicators['FIBONACCI']:
            high = df['high'].max()
            low = df['low'].min()
            diff = high - low
            levels = self.indicator_params['FIBONACCI']['parameters']['levels']
            fib_levels = {level: low + level * diff for level in levels}
            current_price = df['close'].iloc[-1]
            
            signals['FIBONACCI'] = self._get_fibonacci_signal(current_price, fib_levels)
            for level in levels:
                # Use underscore instead of decimal point to avoid issues in JavaScript
                level_str = str(level).replace('.', '_')
                values[f'FIB_{level_str}'] = round(float(fib_levels[level]), 4)
            
        return signals, values
    
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
                
                if current_value > 1:  # 2 dolar yerine 1 dolar olarak değiştirildi
                    self.console.print(f"[yellow]Already holding {current_qty} {base_currency} worth {current_value:.2f} USDT (>1 USDT), skipping buy[/yellow]")
                    return None
                
                # For buy orders, always use 5.5
                qty = 10
                
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
            
            # If it's a buy order, save the position data and set stop loss and take profit
            if side == "Buy":
                base_currency = symbol[:-4] if symbol.endswith('USDT') else symbol.split('USDT')[0]
                current_position = self.positions.get(base_currency, {})
                current_total = float(current_position.get("total", 0))
                
                # Add new quantity to total
                new_total = current_total + float(qty)
                
                # Get take profit and stop loss percentages from config
                take_profit_percent = config.TAKE_PROFIT_PERCENT
                stop_loss_percent = config.STOP_LOSS_PERCENT
                
                # Calculate take profit and stop loss prices
                take_profit_price = current_price * (1 + take_profit_percent)
                stop_loss_price = current_price * (1 - stop_loss_percent)
                
                self.positions[base_currency] = {
                    "total": new_total,
                    "entry_price": current_price,
                    "stop_loss": stop_loss_price,
                    "take_profit": take_profit_price
                }
                
                self.console.print(f"[green]Updated position for {base_currency}:[/green]")
                self.console.print(f"Previous total: {current_total}")
                self.console.print(f"New purchase: {qty}")
                self.console.print(f"New total: {new_total}")
                
                # Set stop loss and take profit
                self.set_risk_management(symbol, current_price, new_total)
                
                self.save_positions_to_file()
            elif side == "Sell":
                # If it's a sell order, remove the position data for this coin
                base_currency = symbol[:-4] if symbol.endswith('USDT') else symbol.split('USDT')[0]
                
                # Cancel any existing limit sell orders for this symbol
                self.cancel_all_orders(symbol)
                
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
        table.add_column("Signal", justify="center", style="bold")
        
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
            
            # Add combined signal
            combined_signal = data.get('combined_signal', 'NEUTRAL')
            signal_color = "green" if combined_signal == 'BUY' else "red" if combined_signal == 'SELL' else "white"
            row.append(f"[{signal_color}]{combined_signal}[/{signal_color}]")
            
            table.add_row(*row)
        
        self.console.print(table)
    
    def run(self):
        """Main bot loop"""
        self.console.print(Panel.fit("[bold green]Trading Bot Started[/bold green]", title="Status"))
        
        while True:
            try:
                # Step 1: Check existing positions for stop loss and take profit
                if self.enable_position_checks:
                    self.console.print("[bold blue]Step 1: Checking existing positions...[/bold blue]")
                    self.check_positions()
                else:
                    self.console.print("[yellow]Step 1: Position checks are disabled, skipping...[/yellow]")
                
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
            # Eğer position_checks devre dışı bırakıldıysa, hiçbir şey yapma
            if not self.enable_position_checks:
                self.console.print("[yellow]Position checks are disabled in config.json[/yellow]")
                return
                
            # Print current positions summary
            self.console.print("[bold blue]Checking current positions...[/bold blue]")
            
            if not self.positions:
                self.console.print("[yellow]No positions found![/yellow]")
                return
                
            self.console.print(f"[cyan]Found {len(self.positions)} positions[/cyan]")
            
            # Update positions first to get fresh wallet data
            wallet_positions = self.get_positions()
            
            # Keep track of how many positions were checked
            positions_checked = 0
            
            # Check all positions (both from wallet and from saved file)
            for coin, position in self.positions.items():
                # Skip USDT
                if coin == "USDT":
                    continue
                    
                # Skip if no entry price (shouldn't happen, but just in case)
                if not position.get("entry_price"):
                    self.console.print(f"[yellow]No entry price for {coin}, skipping[/yellow]")
                    continue
                    
                positions_checked += 1
                symbol = f"{coin}USDT"
                
                try:
                    # Get current price
                    ticker_response = self.client.get_tickers(category="spot", symbol=symbol)
                    
                    if ticker_response.get("retCode") != 0 or not ticker_response.get("result", {}).get("list"):
                        self.console.print(f"[yellow]Warning: Could not get ticker for {symbol}: {ticker_response.get('retMsg')}[/yellow]")
                        continue
                        
                    ticker = ticker_response["result"]["list"][0]
                    current_price = float(ticker["lastPrice"])
                    
                    # Calculate price change percentage
                    price_change = ((current_price - position["entry_price"]) / position["entry_price"]) * 100
                    
                    # Get stop loss and take profit percentages from config
                    stop_loss_percent = config.STOP_LOSS_PERCENT * 100  # Convert to percentage for display
                    take_profit_percent = config.TAKE_PROFIT_PERCENT * 100  # Convert to percentage for display
                    
                    # Recalculate stop loss and take profit prices using config values
                    stop_loss_price = position["entry_price"] * (1 - config.STOP_LOSS_PERCENT)
                    take_profit_price = position["entry_price"] * (1 + config.TAKE_PROFIT_PERCENT)
                    
                    # Update position with latest values from config
                    position["stop_loss"] = stop_loss_price
                    position["take_profit"] = take_profit_price
                    
                    # Display position status
                    if price_change > 0:
                        price_change_color = "green"
                    else:
                        price_change_color = "red"
                    
                    self.console.print(f"[cyan]Checking {symbol}:[/cyan]")
                    self.console.print(f"Entry: {position['entry_price']:.8f}, Current: {current_price:.8f}, Change: [{price_change_color}]{price_change:.2f}%[/{price_change_color}]")
                    self.console.print(f"Stop Loss: {stop_loss_price:.8f} ({stop_loss_percent:.2f}%), Take Profit: {take_profit_price:.8f} ({take_profit_percent:.2f}%)")
                    
                    # Is position in wallet?
                    if coin in wallet_positions:
                        self.console.print(f"[green]Position is in wallet: {wallet_positions[coin]['total']} {coin}[/green]")
                    else:
                        self.console.print(f"[yellow]Position is not in wallet, may be a saved/tracked position[/yellow]")
                    
                    # Check stop loss - eğer stop loss etkinse
                    if self.enable_stop_loss and current_price <= stop_loss_price:
                        self.console.print(f"[red]Stop Loss triggered for {symbol} at {current_price:.8f}[/red]")
                        # Cancel existing limit sell orders before selling
                        self.cancel_all_orders(symbol)
                        # Only place a sell order if the coin is actually in the wallet
                        if coin in wallet_positions and wallet_positions[coin]['total'] > 0:
                            self.place_order(symbol, "Sell", position["total"])
                        else:
                            self.console.print(f"[yellow]Would sell {symbol} but it's not in wallet[/yellow]")
                            # Remove from positions anyway
                            del self.positions[coin]
                        
                    # Check take profit - eğer take profit etkinse
                    elif self.enable_take_profit and current_price >= take_profit_price:
                        self.console.print(f"[green]Take Profit triggered for {symbol} at {current_price:.8f}[/green]")
                        # Cancel existing limit sell orders before selling
                        self.cancel_all_orders(symbol)
                        # Only place a sell order if the coin is actually in the wallet
                        if coin in wallet_positions and wallet_positions[coin]['total'] > 0:
                            self.place_order(symbol, "Sell", position["total"])
                        else:
                            self.console.print(f"[yellow]Would sell {symbol} but it's not in wallet[/yellow]")
                            # Remove from positions anyway
                            del self.positions[coin]
                    
                    # If not in wallet and not triggering SL/TP, keep tracking
                    self.console.print("---")
                        
                except Exception as e:
                    self.console.print(f"[yellow]Warning: Could not check {symbol}: {str(e)}[/yellow]")
                    continue
            
            # Save positions to file after checking
            self.save_positions_to_file()
            
            # Summary
            self.console.print(f"[bold blue]Checked {positions_checked} positions[/bold blue]")
                    
        except Exception as e:
            self.console.print(f"[bold red]Error checking positions: {str(e)}[/bold red]")
            import traceback
            traceback.print_exc()
    
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

    def has_open_orders(self, symbol):
        """Check if there are any open orders for a symbol"""
        try:
            orders = self.client.get_open_orders(
                category="spot",
                symbol=symbol
            )
            
            if orders and orders.get("retCode") == 0 and orders.get("result", {}).get("list"):
                return True
            return False
            
        except Exception as e:
            self.console.print(f"[yellow]Warning: Could not check open orders for {symbol}: {str(e)}[/yellow]")
            return False

    def place_limit_sell_order(self, symbol, qty, price):
        """Place a limit sell order at specified price"""
        try:
            # Get minimum order size and decimal places
            min_qty, min_order_amt, decimal_places = self.get_min_order_size(symbol)
            if min_qty is None:
                return None
                
            # Round to correct decimal places
            multiplier = 10 ** decimal_places
            qty = math.floor(qty * multiplier) / multiplier
            
            # Make sure quantity meets minimum requirements
            if qty < min_qty:
                self.console.print(f"[yellow]Quantity {qty} is below minimum {min_qty} for limit order[/yellow]")
                return None
                
            # Check if value meets minimum order amount
            value = qty * price
            if value < min_order_amt:
                self.console.print(f"[yellow]Limit order value {value:.2f} USDT is below minimum {min_order_amt} USDT[/yellow]")
                return None
                
            # Place the limit sell order
            order = self.client.place_order(
                category="spot",
                symbol=symbol,
                side="Sell",
                orderType="LIMIT",
                qty=str(qty),
                price=str(price),
                timeInForce="GTC"  # Good Till Cancelled
            )
            
            self.console.print(f"[bold green]✓ Limit Sell order placed for {qty} {symbol} at price {price:.8f} (5% above purchase)[/bold green]")
            return order
            
        except Exception as e:
            self.console.print(f"[bold red]Error placing limit sell order: {str(e)}[/bold red]")
            return None
            
    def cancel_all_orders(self, symbol):
        """Cancel all open orders for a symbol"""
        try:
            # Get open orders for the symbol
            orders = self.client.get_open_orders(
                category="spot",
                symbol=symbol
            )
            
            if orders and orders.get("retCode") == 0 and orders.get("result", {}).get("list"):
                # Cancel all open orders
                result = self.client.cancel_all_orders(
                    category="spot",
                    symbol=symbol
                )
                
                if result and result.get("retCode") == 0:
                    self.console.print(f"[green]Successfully cancelled all open orders for {symbol}[/green]")
                    return True
                else:
                    self.console.print(f"[yellow]Failed to cancel orders for {symbol}: {result}[/yellow]")
                    return False
            else:
                self.console.print(f"[cyan]No open orders to cancel for {symbol}[/cyan]")
                return True
                
        except Exception as e:
            self.console.print(f"[bold red]Error cancelling orders: {str(e)}[/bold red]")
            return False

    def set_risk_management(self, symbol, entry_price, position_size):
        """
        Set take profit and stop loss for a position
        
        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            entry_price: Position entry price
            position_size: Size of the position
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Get take profit and stop loss percentages from config
            take_profit_percent = config.TAKE_PROFIT_PERCENT
            stop_loss_percent = config.STOP_LOSS_PERCENT
            
            # Calculate take profit and stop loss prices
            take_profit_price = entry_price * (1 + take_profit_percent)
            stop_loss_price = entry_price * (1 - stop_loss_percent)
            
            # Format for API
            take_profit_str = f"{take_profit_price:.2f}"
            stop_loss_str = f"{stop_loss_price:.2f}"
            
            self.console.print(f"[cyan]Setting risk management for {symbol}:[/cyan]")
            self.console.print(f"Entry Price: {entry_price:.8f}")
            self.console.print(f"Stop Loss: {stop_loss_price:.8f} ({stop_loss_percent*100:.2f}%)")
            self.console.print(f"Take Profit: {take_profit_price:.8f} ({take_profit_percent*100:.2f}%)")
            
            # For futures trading only
            if symbol.endswith("USDT") and symbol in ["BTCUSDT", "ETHUSDT"]:  # Major pairs only
                try:
                    # Set trading stop via API
                    response = self.client.set_trading_stop(
                        category="linear",
                        symbol=symbol,
                        takeProfit=take_profit_str,
                        stopLoss=stop_loss_str,
                        tpTriggerBy="MarkPrice",
                        slTriggerBy="MarkPrice",
                        positionIdx=0  # 0 for one-way mode
                    )
                    
                    if response["retCode"] == 0:
                        self.console.print(f"[green]Successfully set stop loss and take profit for {symbol}[/green]")
                        return True
                    else:
                        self.console.print(f"[yellow]Failed to set stop loss and take profit: {response['retMsg']}[/yellow]")
                        return False
                        
                except Exception as e:
                    self.console.print(f"[yellow]Error setting stop loss and take profit via API: {str(e)}[/yellow]")
                    return False
            
            # For spot trading, we'll use limit sell orders at take profit price
            else:
                # Place a limit sell order at take profit price
                result = self.place_limit_sell_order(symbol, position_size, take_profit_price)
                return result is not None
                
        except Exception as e:
            self.console.print(f"[bold red]Error setting risk management: {str(e)}[/bold red]")
            return False

    def add_test_position(self, symbol, quantity):
        """
        Manually add a test position to the bot
        
        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            quantity: Quantity to add
            
        Returns:
            bool: True if successful
        """
        try:
            # Check that symbol is valid
            if not symbol.endswith("USDT"):
                self.console.print(f"[yellow]Symbol must end with USDT: {symbol}[/yellow]")
                return False
                
            # Extract coin from symbol (e.g. "BTC" from "BTCUSDT")
            coin = symbol[:-4] if symbol.endswith('USDT') else symbol.split('USDT')[0]
            
            # Get current price from API
            try:
                ticker_response = self.client.get_tickers(category="spot", symbol=symbol)
                if ticker_response.get("retCode") != 0 or not ticker_response.get("result", {}).get("list"):
                    self.console.print(f"[yellow]Warning: Could not get ticker for {symbol}: {ticker_response.get('retMsg')}[/yellow]")
                    return False
                    
                ticker = ticker_response["result"]["list"][0]
                current_price = float(ticker["lastPrice"])
            except Exception as e:
                self.console.print(f"[yellow]Warning: Could not get price for {symbol}: {str(e)}[/yellow]")
                return False
                
            # Calculate stop loss and take profit
            stop_loss = current_price * (1 - self.stop_loss_percentage / 100)
            take_profit = current_price * (1 + self.take_profit_percentage / 100)
            
            # Add or update position
            if coin in self.positions:
                # Update existing position
                self.positions[coin]["total"] += float(quantity)
                self.positions[coin]["free"] += float(quantity)
                self.positions[coin]["entry_price"] = current_price
                self.positions[coin]["stop_loss"] = stop_loss
                self.positions[coin]["take_profit"] = take_profit
                self.console.print(f"[green]Updated test position for {coin}[/green]")
            else:
                # Create new position
                self.positions[coin] = {
                    "free": float(quantity),
                    "locked": 0,
                    "total": float(quantity),
                    "entry_price": current_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit
                }
                self.console.print(f"[green]Added new test position for {coin}[/green]")
            
            # Display position details
            self.console.print(f"[cyan]Position details for {coin}:[/cyan]")
            self.console.print(f"Quantity: {self.positions[coin]['total']}")
            self.console.print(f"Entry price: {self.positions[coin]['entry_price']:.8f}")
            self.console.print(f"Current price: {current_price:.8f}")
            self.console.print(f"Stop Loss: {self.positions[coin]['stop_loss']:.8f} ({self.stop_loss_percentage:.2f}%)")
            self.console.print(f"Take Profit: {self.positions[coin]['take_profit']:.8f} ({self.take_profit_percentage:.2f}%)")
            
            # Set trading stop for futures if applicable
            if symbol.endswith("USDT") and symbol in ["BTCUSDT", "ETHUSDT"]:  # Major pairs only
                self.set_risk_management(symbol, current_price, float(quantity))
                
            # Save positions to file
            self.save_positions_to_file()
            return True
            
        except Exception as e:
            self.console.print(f"[bold red]Error adding test position: {str(e)}[/bold red]")
            return False

if __name__ == "__main__":
    load_dotenv()
    
    import argparse
    
    parser = argparse.ArgumentParser(description='Trading Bot')
    parser.add_argument('--testnet', action='store_true', help='Use testnet instead of mainnet')
    parser.add_argument('--config', type=str, default='config.json', help='Path to configuration file')
    parser.add_argument('--test-position', action='store_true', help='Add a test position for BTC')
    parser.add_argument('--symbol', type=str, default='BTCUSDT', help='Symbol for test position')
    parser.add_argument('--quantity', type=float, default=0.001, help='Quantity for test position')
    
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
    
    # Add test position if requested
    if args.test_position:
        console.print(f"[yellow]Adding test position for {args.symbol}[/yellow]")
        bot.add_test_position(args.symbol, args.quantity)
        console.print("[green]Test position added. Continuing with normal operation...[/green]")
    
    bot.run() 
