# Crypto Trading Bot

A cryptocurrency trading bot for Bybit exchange that uses technical indicators for automated trading.

## Features

- Real-time market analysis
- Multiple technical indicators support (RSI, MACD, Bollinger Bands, etc.)
- Automatic trade execution
- Stop-loss and take-profit management
- Position tracking
- High volume pair filtering
- Parallel market analysis

## Requirements

- Python 3.8+
- Bybit API credentials

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/tradingbot.git
cd tradingbot
```

2. Create a virtual environment and activate it:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install required packages:
```bash
pip install -r requirements.txt
```

4. Create a .env file with your Bybit API credentials:
```
BYBIT_API_KEY=your_api_key
BYBIT_API_SECRET=your_api_secret
```

## Configuration

Edit `config.json` to customize:
- Technical indicators
- Trading parameters
- Stop-loss and take-profit percentages
- Minimum volume requirements
- Update intervals

Example configuration:
```json
{
    "indicators": {
        "RSI": {
            "enabled": true,
            "parameters": {
                "period": 14,
                "oversold": 30,
                "overbought": 70
            }
        }
    },
    "trading": {
        "amount": 1.5,
        "min_volume": 1000000,
        "update_interval": 5,
        "max_workers": 10,
        "stop_loss_percentage": 2,
        "take_profit_percentage": 3
    }
}
```

## Usage

Run the bot:
```bash
python trading_bot.py
```

## Safety Features

- Minimum order amount checks
- Balance verification before trades
- Error handling and logging
- Position tracking and management

## Disclaimer

This bot is for educational purposes only. Cryptocurrency trading carries significant risks. Use at your own risk.

## License

MIT License 