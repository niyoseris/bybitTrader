import logging
import config
from pybit.unified_trading import HTTP
import pandas as pd

logger = logging.getLogger(__name__)

def fetch_klines(symbol, timeframe, limit):
    """
    Fetch historical klines/candlestick data from Bybit
    
    Args:
        symbol: Trading pair (e.g. "BTCUSDT")
        timeframe: Time interval (e.g. "15m", "1h")
        limit: Number of candles to fetch
        
    Returns:
        List of klines or None on error
    """
    try:
        # Create Bybit client
        client = HTTP(
            testnet=config.USE_TESTNET,
            api_key=None,  # Read-only operation, no authentication needed
            api_secret=None
        )
        
        # Get klines from Bybit
        response = client.get_kline(
            category="linear",
            symbol=symbol,
            interval=timeframe,
            limit=limit
        )
        
        if response['retCode'] == 0:
            logger.info(f"Successfully fetched {len(response['result']['list'])} klines for {symbol}")
            return response['result']['list']
        else:
            logger.error(f"Failed to fetch klines: {response['retMsg']}")
            return None
            
    except Exception as e:
        logger.error(f"Error fetching klines: {e}")
        return None 