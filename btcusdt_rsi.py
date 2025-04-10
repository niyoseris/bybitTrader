import os
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import time
from datetime import datetime

# Load environment variables
load_dotenv()

def calculate_rma(close_prices, length=14):
    """
    Calculate Relative Momentum Average (RMA) - similar to EMA but with smoother momentum
    Used as the default method for RSI calculation in many platforms like TradingView
    """
    alpha = 1/length
    rma = np.zeros_like(close_prices)
    rma[length] = np.mean(close_prices[1:length+1] - close_prices[:length])
    
    for i in range(length+1, len(close_prices)):
        rma[i] = alpha * (close_prices[i] - close_prices[i-1]) + (1 - alpha) * rma[i-1]
    
    return rma

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

def fetch_btcusdt_data(client, interval="15", limit=100):
    """
    Fetch BTCUSDT kline data from Bybit
    
    Valid intervals for Bybit API are:
    1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M
    
    Note: Bybit API has a maximum limit of 1000 data points per request.
    """
    print(f"Requesting {limit} data points from Bybit API with interval {interval}")
    
    # Ensure limit is within Bybit's constraints (maximum 1000)
    requested_limit = min(limit, 200)
    
    # Make API request
    klines = client.get_kline(
        category="spot",
        symbol="BTCUSDT",
        interval=interval,
        limit=requested_limit
    )
    
    # Create DataFrame
    df = pd.DataFrame(klines["result"]["list"], 
                     columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    
    # Convert to numeric values
    df[['open', 'high', 'low', 'close', 'volume', 'timestamp']] = df[['open', 'high', 'low', 'close', 'volume', 'timestamp']].astype(float)
    
    # Convert timestamp to datetime (first convert to numeric to avoid FutureWarning)
    df['timestamp'] = pd.to_datetime(df['timestamp'].astype(float), unit='ms')
    
    # Sort by timestamp (newest data last)
    df = df.sort_values('timestamp')
    
    print(f"Received {len(df)} data points from Bybit API")
    
    # If we didn't get enough data points and still under the API max limit, make additional requests
    # This part would be needed for implementing pagination if required
    
    return df

def main():
    # Use API credentials from .env file
    api_key = os.environ.get('BYBIT_API_KEY')
    api_secret = os.environ.get('BYBIT_API_SECRET')
    
    # Create API client
    testnet = os.environ.get('USE_TESTNET', 'True').lower() == 'true'
    client = HTTP(
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet
    )
    
    # Fetch BTCUSDT data - using smaller intervals for more accurate RSI
    # Try with a much larger limit to see how many points we actually get
    df = fetch_btcusdt_data(client, interval="1", limit=1000)
    
    # Calculate RSI using RMA 14
    print(f"Calculating RSI using {len(df)} data points")
    rsi_values = calculate_rsi_with_rma(df['close'].values, length=14)
    
    # Add RSI to dataframe and filter out initial periods where RSI is not defined
    df['rsi'] = rsi_values
    df_with_rsi = df[df['rsi'] > 0].reset_index(drop=True)
    print(f"Have {len(df_with_rsi)} valid RSI data points after filtering")
    
    # Print the last few rows with RSI
    print(f"BTCUSDT RSI (RMA 14) Analysis - {datetime.now()}")
    print("=" * 60)
    print(df_with_rsi[['timestamp', 'close', 'rsi']].tail(10).to_string(index=False))
    print("=" * 60)
    
    # Get the latest RSI value
    latest_rsi = df_with_rsi['rsi'].iloc[-1]
    latest_close = df_with_rsi['close'].iloc[-1]
    
    print(f"Latest BTCUSDT Price: ${latest_close:.2f}")
    print(f"Latest RSI (RMA 14): {latest_rsi:.2f}")
    
    # Basic RSI interpretation with more detailed ranges
    if latest_rsi < 30:
        print("RSI Status: Oversold (RSI < 30) - Potential buying opportunity")
    elif latest_rsi > 50:
        print("RSI Status: Overbought (RSI > 70) - Potential selling opportunity") 
    elif 30 <= latest_rsi < 45:
        print("RSI Status: Neutral-Bearish (30-45)")
    elif 45 <= latest_rsi < 55:
        print("RSI Status: Neutral (45-55)")
    else:  # 55-70
        print("RSI Status: Neutral-Bullish (55-70)")
    
    # Add a summary of RSI changes over time
    print("\nRSI Trend Analysis:")
    print("-" * 40)
    
    # Calculate RSI trends for different timeframes
    last_5_rsi = df_with_rsi['rsi'].tail(5).values
    last_15_rsi = df_with_rsi['rsi'].tail(15).values
    last_30_rsi = df_with_rsi['rsi'].tail(30).values
    
    # Calculate changes
    rsi_change_5min = last_5_rsi[-1] - last_5_rsi[0]
    rsi_change_15min = last_15_rsi[-1] - last_15_rsi[0]
    rsi_change_30min = last_30_rsi[-1] - last_30_rsi[0]
    
    # Print trend information
    print(f"5-minute RSI change: {rsi_change_5min:.2f} points")
    print(f"15-minute RSI change: {rsi_change_15min:.2f} points")
    print(f"30-minute RSI change: {rsi_change_30min:.2f} points")
    
    # Trend interpretation
    print("\nMomentum Direction:")
    if rsi_change_5min > 0 and rsi_change_15min > 0 and rsi_change_30min > 0:
        print("Strong Bullish Momentum (All timeframes rising)")
    elif rsi_change_5min < 0 and rsi_change_15min < 0 and rsi_change_30min < 0:
        print("Strong Bearish Momentum (All timeframes falling)")
    elif rsi_change_5min > 0 and rsi_change_15min > 0:
        print("Bullish Momentum (Short-term rising)")
    elif rsi_change_5min < 0 and rsi_change_15min < 0:
        print("Bearish Momentum (Short-term falling)")
    elif rsi_change_5min > 0 and rsi_change_15min < 0:
        print("Possible Reversal - Short-term bullish but medium-term bearish")
    elif rsi_change_5min < 0 and rsi_change_15min > 0:
        print("Possible Reversal - Short-term bearish but medium-term bullish")
    else:
        print("Mixed Signals - No clear trend direction")

if __name__ == "__main__":
    main() 