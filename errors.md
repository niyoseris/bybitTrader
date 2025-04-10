# Trading Bot Errors and Solutions

## Common Errors

### API Authentication Errors
- **Error**: "API key error" or authentication failures
- **Solution**: Make sure .env file contains correct BYBIT_API_KEY and BYBIT_API_SECRET values

### Order Placement Errors
- **Error**: "Quantity is less than minimum required"
- **Solution**: Check min_qty from get_min_order_size and ensure order quantity meets this requirement

### Balance Errors
- **Error**: "Insufficient USDT balance"
- **Solution**: Make sure you have enough USDT in your account to place buy orders

### Price/Value Errors
- **Error**: "Order value is below minimum"
- **Solution**: Make sure the total value (price * quantity) meets the minimum order amount requirement

### Connection Errors
- **Error**: "Connection refused" or timeouts
- **Solution**: Check internet connection or try again later

## Feature-Specific Issues

### Indicator Calculations
- **Error**: Incorrect or unexpected indicator values
- **Solution**: Verify calculation logic, especially for custom calculations like RMA-based RSI

### Market Analysis
- **Issue**: Not finding any high volume markets
- **Solution**: Adjust min_volume in config.json to a lower value if needed

### Trade Execution
- **Issue**: Orders not being placed despite signals
- **Solution**: Check can_place_order logic and ensure wallet has sufficient balance

## Code Improvements and Fixes

### RSI Calculation
- Used custom RMA-based calculation to match TradingView's implementation instead of the ta library's RSI

### Signal Logic
- Now using only FIBONACCI for BUY decisions
- Now using only RSI for SELL decisions
- Removed confusing multiple indicator combinations

### Order Management
- Simplified order quantity calculation
- Added proper checks for minimum order requirements

## Testing Notes

- Always test with testnet=True before using real funds
- Use small amounts for initial tests
- Verify that risk management is working correctly before deploying
