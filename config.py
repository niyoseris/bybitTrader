# Exchange configuration
USE_TESTNET = True
SYMBOL = "BTCUSDT"
QUOTE_CURRENCY = "USDT"

# Trading parameters
POSITION_SIZE = 0.001  # BTC amount
CHECK_INTERVAL = 300  # 5 minutes between checks

# Data parameters
TIMEFRAME = "15m"
KLINE_LIMIT = 100

# Strategy parameters
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Risk management
TAKE_PROFIT_PERCENT = 0.03  # 3% profit target
STOP_LOSS_PERCENT = 0.015  # 1.5% stop loss