# Trading Bot Documentation

## Project Purpose
The trading bot is designed to automate cryptocurrency trading on the Bybit exchange using technical indicators to generate buy and sell signals. The bot focuses on USDT trading pairs with high daily volume.

## Key Components

### Indicators
The bot leverages multiple technical indicators to make trading decisions:
- **RSI (Relative Strength Index)**: Measures overbought/oversold conditions
- **SMA (Simple Moving Average)**: Identifies trend direction
- **MACD (Moving Average Convergence Divergence)**: Identifies momentum shifts
- **Bollinger Bands**: Identifies volatility and potential price breakouts
- **Fibonacci Retracement**: Identifies potential support/resistance levels

### Signal Generation
- **Buy Signals**: Generated based on enabled indicators in the configuration
- **Sell Signals**: Generated based on enabled indicators in the configuration
- The bot can be configured to use either AND logic (all indicators must agree) or OR logic (any indicator can trigger)
- RSI can be set to directly trigger a sell when exceeding a specified threshold

### Position Management
- The bot tracks positions and manages them based on configuration
- Prevents buying coins already held above a specified threshold (default $1)
- Implements take profit and stop loss mechanisms
- Can be configured to use trailing stops

### Market Selection
- Automatically identifies USDT trading pairs with daily volume exceeding minimum threshold
- Filters markets based on user-defined criteria

## Configuration
The bot is highly configurable through the `config.json` file:
- Indicator settings (periods, thresholds, enabled/disabled)
- Trading parameters (amount per trade, minimum market volume)
- Position management settings (take profit, stop loss, position checks)

## Usage
```bash
# Run in testnet mode
python trading_bot.py --testnet

# Run with specific configuration file
python trading_bot.py --config custom_config.json
```

## Development Workflow
1. Market Identification: Finds high-volume USDT pairs
2. Data Collection: Gathers historical price data for each market
3. Indicator Calculation: Computes technical indicators on the data
4. Signal Generation: Determines buy/sell signals based on indicators
5. Order Execution: Places market orders based on signals
6. Position Management: Monitors and manages active positions
7. Loop: Repeats the process continuously