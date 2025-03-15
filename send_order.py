import json
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from rich.console import Console
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Bybit API credentials
API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')

console = Console()

def get_signature(timestamp, api_key, recv_window, data, secret):
    """Generate signature for the request according to Bybit v5 API"""
    sign_str = timestamp + api_key + recv_window + data
    hash = hmac.new(bytes(secret, "utf-8"), bytes(sign_str, "utf-8"), hashlib.sha256)
    return hash.hexdigest()

def get_instrument_info(symbol):
    """Get instrument information including minimum amounts"""
    url = f"https://api.bybit.com/v5/market/instruments-info?category=spot&symbol={symbol}"
    
    try:
        response = requests.get(url)
        result = response.json()
        
        if result["retCode"] == 0 and len(result["result"]["list"]) > 0:
            info = result["result"]["list"][0]
            
            # Debug: print raw API response
            console.print("\n[cyan]Raw API Response:[/cyan]")
            console.print(json.dumps(result["result"]["list"][0], indent=2))
            
            # Print coin information
            console.print("\n[cyan]Coin Information:[/cyan]")
            console.print(f"Symbol: {info.get('symbol', 'N/A')}")
            console.print(f"Base Coin: {info.get('baseCoin', 'N/A')}")
            console.print(f"Quote Coin: {info.get('quoteCoin', 'N/A')}")
            
            # Use lotSizeFilter for min/max quantities
            lot_filter = info.get('lotSizeFilter', {})
            price_filter = info.get('priceFilter', {})
            
            min_qty = float(lot_filter.get('minOrderQty', '0.1'))
            max_qty = float(lot_filter.get('maxOrderQty', '1000000'))
            min_order_amt = float(price_filter.get('minOrderAmt', '5'))
            max_order_amt = float(price_filter.get('maxOrderAmt', '100000'))
            tick_size = float(price_filter.get('tickSize', '0.0001'))
            
            # Determine precision from tickSize
            base_precision = len(str(float(lot_filter.get('qtyStep', '0.1'))).split('.')[-1])
            quote_precision = len(str(tick_size).split('.')[-1])
            
            console.print(f"Min Order Qty: {min_qty}")
            console.print(f"Max Order Qty: {max_qty}")
            console.print(f"Min Order Amount: {min_order_amt} USDT")
            console.print(f"Max Order Amount: {max_order_amt} USDT")
            console.print(f"Price Tick Size: {tick_size}")
            console.print(f"Base Precision: {base_precision}")
            console.print(f"Quote Precision: {quote_precision}")
            
            return {
                'symbol': info.get('symbol'),
                'baseCoin': info.get('baseCoin'),
                'quoteCoin': info.get('quoteCoin'),
                'minOrderQty': min_qty,
                'maxOrderQty': max_qty,
                'minOrderAmt': min_order_amt,
                'maxOrderAmt': max_order_amt,
                'tickSize': tick_size,
                'basePrecision': base_precision,
                'quotePrecision': quote_precision
            }
        else:
            console.print(f"[red]Error getting instrument info: {result['retMsg']}[/red]")
            return None
            
    except Exception as e:
        console.print(f"[red]Error getting instrument info: {str(e)}[/red]")
        return None

def get_current_price(symbol):
    """Get current price for a symbol"""
    url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
    
    try:
        response = requests.get(url)
        result = response.json()
        
        if result["retCode"] == 0 and len(result["result"]["list"]) > 0:
            price = float(result["result"]["list"][0]["lastPrice"])
            console.print(f"\n[cyan]Current Price:[/cyan] {price} USDT")
            return price
        else:
            console.print(f"[red]Error getting price: {result['retMsg']}[/red]")
            return None
            
    except Exception as e:
        console.print(f"[red]Error getting price: {str(e)}[/red]")
        return None

def calculate_quantity(symbol, usdt_amount=5.1):
    """Calculate quantity based on current price and minimum requirements"""
    info = get_instrument_info(symbol)
    if not info:
        return None
        
    price = get_current_price(symbol)
    if not price:
        return None
        
    # Calculate quantity
    qty = usdt_amount / price
    
    # Round to base precision
    base_precision = int(info["basePrecision"])
    qty = round(qty, base_precision)
    
    # Check minimum quantity
    min_qty = float(info["minOrderQty"])
    if qty < min_qty:
        qty = min_qty
        console.print(f"[yellow]Quantity adjusted to minimum: {qty}[/yellow]")
    
    # Calculate final value
    final_value = qty * price
    
    # Check minimum order amount
    min_order_amt = float(info["minOrderAmt"])
    if final_value < min_order_amt:
        console.print(f"[red]Order value ({final_value:.2f} USDT) is below minimum ({min_order_amt} USDT)[/red]")
        return None
    
    console.print(f"\n[green]Calculated Order:[/green]")
    console.print(f"Quantity: {qty}")
    console.print(f"Estimated Value: {final_value:.2f} USDT")
    
    return str(qty)

def send_order(category, symbol, side, order_type, qty):
    """Send a real order to Bybit"""
    
    # Endpoint
    url = "https://api.bybit.com/v5/order/create"
    
    # Request parameters
    params = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": order_type,
        "qty": str(qty)
    }
    
    # Convert params to JSON string
    data = json.dumps(params)
    
    # Generate timestamp and other required values
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    
    # Generate signature
    signature = get_signature(
        timestamp,
        API_KEY,
        recv_window,
        data,
        API_SECRET
    )
    
    # Headers
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }
    
    try:
        # Send request
        response = requests.post(url, headers=headers, data=data)
        result = response.json()
        
        # Print request details for debugging
        console.print("\n[cyan]Request Details:[/cyan]")
        console.print(f"URL: {url}")
        console.print("Headers:")
        console.print(json.dumps(headers, indent=2))
        console.print("Data:")
        console.print(data)
        
        # Print response
        console.print("\n[yellow]API Response:[/yellow]")
        console.print(json.dumps(result, indent=2))
        
        return result
        
    except Exception as e:
        console.print(f"[red]Error sending order: {str(e)}[/red]")
        return None

if __name__ == "__main__":
    symbol = "OIKUSDT"
    
    # Get coin information and calculate quantity
    qty = calculate_quantity(symbol)
    if not qty:
        console.print("[red]Failed to calculate quantity[/red]")
        exit(1)
    
    # Order details
    order_data = {
        "category": "spot",
        "symbol": symbol,
        "side": "Buy",
        "order_type": "MARKET",
        "qty": 5
    }
    
    console.print("\n[yellow]Sending Order to Bybit:[/yellow]")
    console.print(json.dumps(order_data, indent=2))
    
    # Send the order
    response = send_order(**order_data) 