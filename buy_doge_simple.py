import os
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
import json

# Load environment variables
load_dotenv()
api_key = os.getenv('BYBIT_API_KEY')
api_secret = os.getenv('BYBIT_API_SECRET')

# Initialize Bybit client
client = HTTP(
    api_key=api_key,
    api_secret=api_secret,
    testnet=False  # Use mainnet
)

# Get instrument info for DOGEUSDT
instrument_info = client.get_instruments_info(
    category="spot",
    symbol="DOGEUSDT"
)

# Print instrument info
print("Instrument Info:")
print(json.dumps(instrument_info["result"]["list"][0], indent=2))

# Extract trading constraints
lot_size_filter = instrument_info["result"]["list"][0]["lotSizeFilter"]
min_order_qty = float(lot_size_filter["minOrderQty"])
min_order_amt = float(lot_size_filter["minOrderAmt"])

# Get current price
ticker = client.get_tickers(category="spot", symbol="DOGEUSDT")
current_price = float(ticker["result"]["list"][0]["lastPrice"])

print(f"\nDOGE price: {current_price} USDT")
print(f"Minimum order quantity: {min_order_qty} DOGE")
print(f"Minimum order amount: {min_order_amt} USDT")

# Calculate quantity needed to meet minimum order amount
qty_needed = min_order_amt / current_price
qty_to_buy = max(qty_needed, min_order_qty)

print(f"\nQuantity needed to meet minimum amount: {qty_needed:.2f} DOGE")
print(f"Actual quantity to buy: {qty_to_buy:.2f} DOGE")
print(f"Order value: {qty_to_buy * current_price:.2f} USDT")

# Get account balance
account_info = client.get_wallet_balance(accountType="UNIFIED")
usdt_balance = 0

for asset in account_info["result"]["list"][0]["coin"]:
    if asset["coin"] == "USDT":
        usdt_balance = float(asset["equity"])
        break

print(f"\nUSDT balance: {usdt_balance:.2f} USDT")

# Check if we have enough balance
if usdt_balance < qty_to_buy * current_price:
    print(f"Insufficient balance. Need: {qty_to_buy * current_price:.2f} USDT, Have: {usdt_balance:.2f} USDT")
    exit()

# Ask for confirmation
confirmation = input("\nDo you want to place this order? (yes/no): ")
if confirmation.lower() != "yes":
    print("Order cancelled by user")
    exit()

try:
    # Place order
    order = client.place_order(
        category="spot",
        symbol="DOGEUSDT",
        side="Buy",
        orderType="MARKET",
        qty=str(qty_to_buy)
    )
    
    print("\nOrder placed successfully:")
    print(json.dumps(order, indent=2))
    
except Exception as e:
    print(f"\nError placing order: {str(e)}") 