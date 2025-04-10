import os
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import time
from datetime import datetime
from rich.console import Console
from rich.table import Table

# Initialize Rich console for better formatting
console = Console()

# Load environment variables
load_dotenv()

def calculate_rsi_with_rma(close_prices, length=14):
    """
    Calculate RSI using RMA method with specified length
    """
    # Calculate price changes
    delta = np.zeros_like(close_prices)
    delta[1:] = close_prices[1:] - close_prices[:-1]
    
    # Separate gains and losses
    gains = delta.copy()
    losses = delta.copy()
    gains[gains < 0] = 0
    losses[losses > 0] = 0
    losses = abs(losses)
    
    # Calculate RMA for gains and losses
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
    
    return rsi

def fetch_kline_data(client, symbol, interval="1", limit=1000):
    """
    Fetch kline data from Bybit
    
    Valid intervals for Bybit API are:
    1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M
    """
    # Ensure limit is within Bybit's constraints (maximum 1000)
    requested_limit = min(limit, 1000)
    
    # Make API request
    try:
        klines = client.get_kline(
            category="spot",
            symbol=symbol,
            interval=interval,
            limit=requested_limit
        )
        
        # Check response status
        if 'retCode' in klines and klines['retCode'] != 0:
            print(f"API Error for {symbol}: {klines['retMsg']}")
            return None
        
        # Create DataFrame
        df = pd.DataFrame(klines["result"]["list"], 
                        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        
        # Convert to numeric values
        df[['open', 'high', 'low', 'close', 'volume', 'timestamp']] = df[['open', 'high', 'low', 'close', 'volume', 'timestamp']].astype(float)
        
        # Sort by timestamp (newest data last)
        df = df.sort_values('timestamp')
        
        return df
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None

def get_rsi_for_pair(client, symbol, interval="1", length=14):
    """
    Get RSI value for a specific trading pair
    """
    try:
        # Fetch data
        df = fetch_kline_data(client, symbol, interval, limit=1000)
        if df is None or len(df) < length + 1:  # Need at least enough data for RSI calculation
            return None
        
        # Calculate RSI using RMA
        rsi_values = calculate_rsi_with_rma(df['close'].values, length=length)
        
        # Add RSI to dataframe and filter out initial periods where RSI is not defined
        df['rsi'] = rsi_values
        df_with_rsi = df[df['rsi'] > 0].reset_index(drop=True)
        
        if len(df_with_rsi) == 0:
            return None
            
        # Get the latest data
        latest_rsi = float(df_with_rsi['rsi'].iloc[-1])
        latest_close = float(df_with_rsi['close'].iloc[-1])
        
        # Get last 5 values to calculate trend
        if len(df_with_rsi) >= 5:
            last_5_rsi = df_with_rsi['rsi'].tail(5).values
            rsi_change = last_5_rsi[-1] - last_5_rsi[0]
        else:
            rsi_change = 0
            
        return {
            'symbol': symbol,
            'rsi': latest_rsi,
            'price': latest_close,
            'trend': rsi_change
        }
    except Exception as e:
        print(f"Error calculating RSI for {symbol}: {e}")
        return None

def get_rsi_status(rsi_value):
    """Get RSI status text and color"""
    if rsi_value < 30:
        return "OVERSOLD", "green"
    elif rsi_value > 70:
        return "OVERBOUGHT", "red"
    elif 30 <= rsi_value < 45:
        return "NEUTRAL-BEARISH", "yellow"
    elif 45 <= rsi_value < 55:
        return "NEUTRAL", "white"
    else:  # 55-70
        return "NEUTRAL-BULLISH", "cyan"

def get_high_volume_pairs(client, min_volume=5_000_000):
    """
    Get trading pairs with daily volume > minimum volume (default 5M USD)
    Returns list of symbol names like "BTCUSDT"
    """
    try:
        console.print("[bold blue]Identifying high volume markets...[/bold blue]")
        tickers = client.get_tickers(category="spot")
        high_volume_pairs = []
        
        for ticker in tickers["result"]["list"]:
            symbol = ticker["symbol"]
            if symbol.endswith("USDT"):
                try:
                    volume_24h = float(ticker["volume24h"]) * float(ticker["lastPrice"])
                    if volume_24h > min_volume:
                        high_volume_pairs.append(symbol)
                except (ValueError, KeyError):
                    continue
        
        console.print(f"[green]Found {len(high_volume_pairs)} markets with >${min_volume/1_000_000:.1f}M daily volume[/green]")
        return high_volume_pairs
    except Exception as e:
        console.print(f"[bold red]Error getting high volume pairs: {e}[/bold red]")
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"]  # Return popular pairs as fallback

def display_rsi_table(rsi_data, sort_by="rsi", reverse=False):
    """Display RSI data in a nicely formatted table"""
    if not rsi_data:
        console.print("[bold red]No valid RSI data available[/bold red]")
        return
    
    # Sort data
    if sort_by == "symbol":
        sorted_data = sorted(rsi_data, key=lambda x: x["symbol"])
    elif sort_by == "price":
        sorted_data = sorted(rsi_data, key=lambda x: x["price"], reverse=reverse)
    elif sort_by == "trend":
        sorted_data = sorted(rsi_data, key=lambda x: x["trend"], reverse=reverse)
    else:  # Default sort by RSI
        sorted_data = sorted(rsi_data, key=lambda x: x["rsi"], reverse=reverse)
    
    # Create table
    table = Table(title=f"Crypto RSI Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Add columns
    table.add_column("Symbol", style="bold")
    table.add_column("Price", justify="right")
    table.add_column("RSI (14)", justify="right")
    table.add_column("Status", justify="center")
    table.add_column("Trend", justify="right")
    
    # Add rows
    for data in sorted_data:
        symbol = data["symbol"]
        price = f"${data['price']:.4f}"
        rsi = f"{data['rsi']:.2f}"
        status, color = get_rsi_status(data["rsi"])
        
        # Trend formatting
        trend = data.get("trend", 0)
        if trend > 0:
            trend_text = f"[green]+{trend:.2f}[/green]"
        elif trend < 0:
            trend_text = f"[red]{trend:.2f}[/red]"
        else:
            trend_text = f"[white]{trend:.2f}[/white]"
        
        table.add_row(
            symbol,
            price,
            rsi,
            f"[{color}]{status}[/{color}]",
            trend_text
        )
    
    # Print table
    console.print(table)

def monitor_rsi_for_pairs(symbols=None, interval="1", refresh_seconds=60, use_testnet=False, min_volume=5_000_000, sort="rsi"):
    """
    Monitor RSI for multiple trading pairs with periodic refresh
    
    Args:
        symbols: List of trading pairs to monitor. If None, will use high volume pairs
        interval: Interval for kline data (1, 5, 15, 30, 60, etc.)
        refresh_seconds: How often to refresh the data
        use_testnet: Whether to use testnet (default False for real data)
        min_volume: Minimum 24h volume in USD for auto-selecting pairs
        sort: How to sort results ('rsi', 'symbol', 'price', 'trend')
    """
    try:
        # Use API credentials from .env file
        api_key = os.environ.get('BYBIT_API_KEY')
        api_secret = os.environ.get('BYBIT_API_SECRET')
        
        # Create API client
        client = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=use_testnet
        )
        
        console.print(f"[bold]{'TEST MODE' if use_testnet else 'LIVE MODE'} - RSI Monitor with RMA-14[/bold]")
        
        # Get symbols to monitor if not provided
        if symbols is None:
            symbols = get_high_volume_pairs(client, min_volume)
        
        if not symbols:
            console.print("[bold red]No symbols to monitor![/bold red]")
            return
            
        console.print(f"[bold blue]Monitoring {len(symbols)} trading pairs ([italic]{', '.join(symbols[:5])}{', ...' if len(symbols) > 5 else ''}[/italic])[/bold blue]")
        console.print(f"[bold blue]Interval: {interval}, Refresh: {refresh_seconds}s, Sort by: {sort}[/bold blue]")
        console.print("[yellow]Press Ctrl+C to exit[/yellow]")
        
        # Main monitoring loop
        first_run = True
        
        while True:
            try:
                start_time = time.time()
                
                # Get RSI data for all symbols
                rsi_data = []
                console.print(f"\n[bold]Fetching data for {len(symbols)} pairs...[/bold]", end="")
                
                for symbol in symbols:
                    result = get_rsi_for_pair(client, symbol, interval)
                    if result:
                        rsi_data.append(result)
                    
                    # Small delay between requests to avoid API rate limits
                    time.sleep(0.1)
                
                # Display results
                console.clear()
                display_rsi_table(rsi_data, sort_by=sort)
                
                # Wait for next refresh
                elapsed = time.time() - start_time
                sleep_time = max(1, refresh_seconds - elapsed)
                
                if not first_run:
                    console.print(f"[dim]Next update in {int(sleep_time)} seconds...[/dim]")
                
                first_run = False
                time.sleep(sleep_time)
                
            except Exception as e:
                console.print(f"[bold red]Error in monitoring loop: {e}[/bold red]")
                time.sleep(5)  # Wait a bit before retrying
                
    except KeyboardInterrupt:
        console.print("\n[bold yellow]RSI monitoring stopped by user[/bold yellow]")
    except Exception as e:
        console.print(f"[bold red]Fatal error: {e}[/bold red]")

if __name__ == "__main__":
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Crypto RSI Monitor with RMA-14")
    parser.add_argument("-s", "--symbols", help="Comma-separated list of trading pairs (e.g. BTCUSDT,ETHUSDT)", type=str)
    parser.add_argument("-i", "--interval", help="Kline interval (1,5,15,30,60,etc)", default="1")
    parser.add_argument("-r", "--refresh", help="Refresh interval in seconds", type=int, default=60)
    parser.add_argument("-t", "--testnet", help="Use testnet instead of real data", action="store_true")
    parser.add_argument("-v", "--volume", help="Minimum 24h volume in millions USD", type=float, default=5.0)
    parser.add_argument("--sort", help="Sort results by (rsi,symbol,price,trend)", default="rsi")
    
    args = parser.parse_args()
    
    # Process symbols
    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    
    # Run the monitor
    monitor_rsi_for_pairs(
        symbols=symbols,
        interval=args.interval,
        refresh_seconds=args.refresh,
        use_testnet=args.testnet,
        min_volume=args.volume * 1_000_000,
        sort=args.sort
    ) 