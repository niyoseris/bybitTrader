import os
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import time
from datetime import datetime

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

def fetch_btcusdt_data(client, interval="1", limit=1000, symbol="BTCUSDT"):
    """
    Fetch BTCUSDT kline data from Bybit
    
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
            print(f"API Error: {klines['retMsg']}")
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
        print(f"Error fetching data: {e}")
        return None

def get_current_rsi(use_testnet=False):
    """
    Get only the current RSI value for BTCUSDT
    """
    try:
        # Use API credentials from .env file
        api_key = os.environ.get('BYBIT_API_KEY')
        api_secret = os.environ.get('BYBIT_API_SECRET')
        
        # Force testnet to False to use real market data
        client = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=use_testnet
        )
        
        # Fetch BTCUSDT data - using 1-minute interval data
        df = fetch_btcusdt_data(client, interval="1", limit=1000, symbol="XRPUSDT")
        if df is None or len(df) < 15:  # Need at least enough data for RSI calculation
            return None, None, None
        
        # Calculate RSI using RMA 14
        rsi_values = calculate_rsi_with_rma(df['close'].values, length=14)
        
        # Add RSI to dataframe and filter out initial periods where RSI is not defined
        df['rsi'] = rsi_values
        df_with_rsi = df[df['rsi'] > 0].reset_index(drop=True)
        
        if len(df_with_rsi) == 0:
            return None, None, None
            
        # Get the latest data
        latest_rsi = df_with_rsi['rsi'].iloc[-1]
        latest_close = df_with_rsi['close'].iloc[-1]
        latest_time = datetime.now()  # Use current time instead of timestamp from API
        
        return latest_rsi, latest_close, latest_time
    except Exception as e:
        print(f"Error getting RSI: {e}")
        return None, None, None

def print_rsi_status(rsi, price, timestamp=None):
    """Print RSI status with formatting"""
    if rsi is not None:
        # Time info
        time_str = ""
        if timestamp is not None:
            time_str = f" | {timestamp.strftime('%H:%M:%S')}"
            
        print(f"BTCUSDT: ${price:.2f} | RSI(14): {rsi:.2f}{time_str}")
        
        # RSI interpretation
        if rsi < 30:
            print("Status: OVERSOLD - Potential buying opportunity")
        elif rsi > 70:
            print("Status: OVERBOUGHT - Potential selling opportunity") 
        elif 30 <= rsi < 45:
            print("Status: NEUTRAL-BEARISH")
        elif 45 <= rsi < 55:
            print("Status: NEUTRAL")
        else:  # 55-70
            print("Status: NEUTRAL-BULLISH")

        print(rsi)
        return rsi
    else:
        print("Failed to get RSI data")

if __name__ == "__main__":
    import sys
    
    # Check for command-line arguments
    refresh_mode = False
    use_testnet = False
    
    if len(sys.argv) > 1:
        if 'refresh' in sys.argv or '-r' in sys.argv:
            refresh_mode = True
        if 'testnet' in sys.argv or '-t' in sys.argv:
            use_testnet = True
    
    if refresh_mode:
        try:
            print("RSI Monitor Mode - Press Ctrl+C to exit")
            print("=" * 50)
            
            # First run
            rsi, price, timestamp = get_current_rsi(use_testnet)
            print_rsi_status(rsi, price, timestamp)
            prev_rsi = rsi
            
            while True:
                time.sleep(30)  # Wait for 30 seconds
                rsi, price, timestamp = get_current_rsi(use_testnet)
                
                # Only print if RSI changed
                if rsi is not None and (prev_rsi is None or abs(rsi - prev_rsi) > 0.01):
                    print("\n" + "=" * 50)
                    print_rsi_status(rsi, price, timestamp)
                    prev_rsi = rsi
                else:
                    print(".", end="", flush=True)  # Show activity
                
        except KeyboardInterrupt:
            print("\nRSI monitoring stopped")
    else:
        # Single run mode
        rsi, price, timestamp = get_current_rsi(use_testnet)
        print_rsi_status(rsi, price, timestamp) 