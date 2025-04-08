import os
import json
import pandas as pd
import numpy as np
from datetime import datetime
import threading
import concurrent.futures
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from pybit.unified_trading import HTTP
from ta.trend import SMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from dotenv import load_dotenv
import config
import argparse
import time

# Set up console for rich text display
console = Console()

class IndicatorAnalyzer:
    def __init__(self, api_key=None, api_secret=None, config_path='config.json', testnet=True, api_url=None):
        """
        Initialize the indicator analyzer
        
        Args:
            api_key: Bybit API key
            api_secret: Bybit API secret
            config_path: Path to the configuration file
            testnet: Whether to use the testnet
            api_url: URL of the Flask API to send data to
        """
        # Load API keys from .env if not provided
        if not api_key or not api_secret:
            load_dotenv()
            api_key = 'TEST'
            api_secret = 'TEST'
        
        # Initialize Bybit client
        self.client = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet
        )
        
        # Add mutex for thread safety
        self.market_data_lock = Lock()
        
        # Flask API URL
        self.api_url = api_url
        
        # Load configuration
        self.load_config(config_path)
        
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
            self.min_volume = config_json['trading']['min_volume']
            self.kline_interval = str(config_json['trading']['kline']['interval'])
            self.kline_limit = int(config_json['trading']['kline']['limit'])
            self.max_workers = config_json['trading'].get('max_workers', 10)  # Default to 10 threads if not specified
            
            console.print(f"[green]Configuration loaded successfully from {config_path}[/green]")
            console.print(f"[blue]Using {self.kline_interval} minute candles, fetching last {self.kline_limit} candles[/blue]")
            
            console.print("[blue]Active indicators for BUY signals:[/blue]")
            for indicator, enabled in self.buy_indicators.items():
                if enabled:
                    console.print(f"  - {indicator}")
            
            console.print("[blue]Active indicators for SELL signals:[/blue]")
            for indicator, enabled in self.sell_indicators.items():
                if enabled:
                    console.print(f"  - {indicator}")
            
            console.print(f"[blue]Using {self.max_workers} parallel threads for analysis[/blue]")
            if self.api_url:
                console.print(f"[blue]Will send results to API: {self.api_url}[/blue]")
        except Exception as e:
            console.print(f"[bold red]Error loading config: {str(e)}[/bold red]")
            raise

    def get_high_volume_pairs(self):
        """Get USDT pairs with daily volume > minimum volume"""
        console.print("[bold blue]Identifying high volume markets...[/bold blue]")
        tickers = self.client.get_tickers(category="spot")
        high_volume_pairs = []
        
        for ticker in tickers["result"]["list"]:
            symbol = ticker["symbol"]
            if symbol.endswith("USDT"):
                volume_24h = float(ticker["volume24h"]) * float(ticker["lastPrice"])
                if volume_24h > self.min_volume:
                    high_volume_pairs.append(symbol)
        
        console.print(f"[green]Found {len(high_volume_pairs)} markets with >${self.min_volume/1_000_000:.1f}M daily volume[/green]")
        return high_volume_pairs
    
    def get_klines(self, symbol):
        """Get historical klines/candlestick data"""
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
            return df
        except Exception as e:
            console.print(f"[bold red]Error getting klines for {symbol}: {str(e)}[/bold red]")
            return None
    
    def calculate_indicators(self, df):
        """Calculate technical indicators"""
        signals = {}
        values = {}  # İndikatörlerin gerçek değerleri için
        
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
            values['BB_Width'] = round(float((bb_upper - bb_lower) / bb_middle * 100), 2)  # % Band genişliği
            
        if self.active_indicators['FIBONACCI']:
            high = df['high'].max()
            low = df['low'].min()
            diff = high - low
            levels = self.indicator_params['FIBONACCI']['parameters']['levels']
            fib_levels = {level: low + level * diff for level in levels}
            current_price = df['close'].iloc[-1]
            
            signals['FIBONACCI'] = self._get_fibonacci_signal(current_price, fib_levels)
            for level in levels:
                # Decimal nokta ile sorun çıkmasın diye _ kullanıyoruz
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
    
    def analyze_market(self, pair):
        """Analyze a single market"""
        try:
            # Get market data
            df = self.get_klines(pair)
            if df is None or len(df) < 30:
                # console.print(f"[yellow]Insufficient data for {pair}, skipping[/yellow]")
                return pair, None
                
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
            
            # Alış sinyalleri: enabled_for_buy olan TÜM indikatörler alış sinyali veriyorsa
            all_buy_signals = True
            for indicator in active_buy_indicators:
                if indicator in signals and signals[indicator] not in ['BUY', 'STRONG_BUY']:
                    all_buy_signals = False
                    break
            
            # Satış sinyalleri: enabled_for_sell olan TÜM indikatörler satış sinyali veriyorsa
            all_sell_signals = True
            for indicator in active_sell_indicators:
                if indicator in signals and signals[indicator] != 'SELL':
                    all_sell_signals = False
                    break
            
            # Combined signal oluştur
            if all_buy_signals and active_buy_indicators:
                market_info['combined_signal'] = 'BUY'
                with self.market_data_lock:
                    console.print(f"[bold green]Buy Signal detected for {pair} (All buy indicators: {', '.join(active_buy_indicators)})[/bold green]")
            elif all_sell_signals and active_sell_indicators:
                market_info['combined_signal'] = 'SELL'
                with self.market_data_lock:
                    console.print(f"[bold red]Sell Signal detected for {pair} (All sell indicators: {', '.join(active_sell_indicators)})[/bold red]")
            else:
                market_info['combined_signal'] = 'NEUTRAL'
                
            return pair, market_info
            
        except Exception as e:
            with self.market_data_lock:
                console.print(f"[bold red]Error analyzing {pair}: {str(e)}[/bold red]")
            return pair, None
    
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
        
        console.print(table)
    
    def send_data_to_api(self, market_data):
        """Send market data to Flask API"""
        if not self.api_url:
            console.print("[yellow]No API URL provided, skipping data sending[/yellow]")
            return False
            
        try:
            # Create summary data
            buy_signals = sum(1 for data in market_data.values() if data.get('combined_signal') == 'BUY')
            sell_signals = sum(1 for data in market_data.values() if data.get('combined_signal') == 'SELL')
            neutral_signals = sum(1 for data in market_data.values() if data.get('combined_signal') == 'NEUTRAL')
            
            # Create a clean copy of the market data without NaN values
            clean_market_data = {}
            for symbol, data in market_data.items():
                clean_data = {}
                for key, value in data.items():
                    if isinstance(value, dict):
                        clean_data[key] = {k: (None if pd.isna(v) else v) for k, v in value.items()}
                    elif isinstance(value, float) and pd.isna(value):
                        clean_data[key] = None
                    else:
                        clean_data[key] = value
                clean_market_data[symbol] = clean_data
            
            payload = {
                'market_data': clean_market_data,
                'summary': {
                    'buy_signals': buy_signals,
                    'sell_signals': sell_signals,
                    'neutral_signals': neutral_signals,
                    'total_markets': len(market_data)
                }
            }
            
            console.print(f"[blue]Sending data to API: {self.api_url}[/blue]")
            response = requests.post(
                self.api_url, 
                json=payload,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                console.print(f"[green]Data sent successfully: {response.json()}[/green]")
                return True
            else:
                console.print(f"[bold red]Error sending data to API: {response.status_code} - {response.text}[/bold red]")
                return False
                
        except Exception as e:
            console.print(f"[bold red]Error sending data to API: {str(e)}[/bold red]")
            return False
    
    def analyze_all_markets(self):
        """Analyze all high volume markets and display results using threads"""
        try:
            # Get high volume pairs
            pairs = self.get_high_volume_pairs()
            market_data = {}
            
            # Analyze markets in parallel
            console.print(f"[bold blue]Analyzing {len(pairs)} markets in parallel using {self.max_workers} threads...[/bold blue]")
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True
            ) as progress:
                task = progress.add_task("Analyzing markets...", total=len(pairs))
                
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    # Submit all analysis tasks
                    future_to_pair = {executor.submit(self.analyze_market, pair): pair for pair in pairs}
                    
                    # Process results as they complete
                    for future in as_completed(future_to_pair):
                        pair, result = future.result()
                        if result is not None:
                            market_data[pair] = result
                        progress.advance(task)
            
            # Display results
            console.print("\n[bold blue]Market Analysis Results:[/bold blue]")
            self.display_market_data(market_data)
            
            # Print summary
            buy_signals = sum(1 for data in market_data.values() if data.get('combined_signal') == 'BUY')
            sell_signals = sum(1 for data in market_data.values() if data.get('combined_signal') == 'SELL')
            neutral_signals = sum(1 for data in market_data.values() if data.get('combined_signal') == 'NEUTRAL')
            
            console.print(f"\n[bold]Summary:[/bold]")
            console.print(f"[green]Buy signals: {buy_signals}[/green]")
            console.print(f"[red]Sell signals: {sell_signals}[/red]")
            console.print(f"[white]Neutral signals: {neutral_signals}[/white]")
            console.print(f"[cyan]Total markets analyzed: {len(market_data)}[/cyan]")
            
            # Send data to API if URL is provided
            if self.api_url:
                self.send_data_to_api(market_data)
            
            return market_data
        
        except Exception as e:
            console.print(f"[bold red]Error in market analysis: {str(e)}[/bold red]")
            return {}


def main():
    """Main function"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Analyze markets and send data to API')
    parser.add_argument('--api-url', type=str, default='http://localhost:5005/api/market-data',
                      help='URL of the Flask API to send data to')
    parser.add_argument('--testnet', action='store_true', help='Use Bybit testnet')
    parser.add_argument('--config', type=str, default='config.json', help='Path to config file')
    args = parser.parse_args()
    
    # Display welcome message
    console.print(Panel.fit("[bold green]Bybit Market Indicator Analysis[/bold green]", title="Market Scanner"))
    
    # Load API keys from environment variables
    load_dotenv()
    api_key = 'TEST'
    api_secret = 'TEST'
    
    # Check if API keys are available
    if not api_key or not api_secret:
        console.print("[bold red]API keys not found. Please set BYBIT_API_KEY and BYBIT_API_SECRET in .env file.[/bold red]")
        return
    
    # Initialize analyzer
    analyzer = IndicatorAnalyzer(
        api_key=api_key, 
        api_secret=api_secret, 
        config_path=args.config, 
        testnet=args.testnet,
        api_url=args.api_url
    )
    
    # Analyze all markets
    analyzer.analyze_all_markets()


if __name__ == "__main__":
    while True:
        main()
        time.sleep(10)
