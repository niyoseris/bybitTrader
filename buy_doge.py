import os
import json
import time
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
from rich.console import Console

# Load environment variables
load_dotenv()
api_key = os.getenv('BYBIT_API_KEY')
api_secret = os.getenv('BYBIT_API_SECRET')

# Initialize console for pretty output
console = Console()

def buy_doge():
    """Buy DOGE with available balance"""
    try:
        # Initialize Bybit client
        client = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=False  # Use mainnet
        )
        
        symbol = "DOGEUSDT"
        
        # Get account balance first
        try:
            account_info = client.get_wallet_balance(accountType="UNIFIED")
            console.print(f"[cyan]Account info received[/cyan]")
            
            usdt_balance = 0
            
            # Check if the response has the expected structure
            if "result" in account_info and "list" in account_info["result"] and len(account_info["result"]["list"]) > 0:
                for asset in account_info["result"]["list"][0]["coin"]:
                    if asset["coin"] == "USDT":
                        equity = float(asset["equity"])
                        console.print(f"[cyan]USDT equity: {equity}[/cyan]")
                        usdt_balance = equity
                        break
            
            console.print(f"[yellow]USDT balance: {usdt_balance:.2f} USDT[/yellow]")
            
            # If balance is too low, exit
            if usdt_balance < 1:
                console.print(f"[bold red]Balance too low for trading. Have: {usdt_balance:.2f} USDT[/bold red]")
                return
                
        except Exception as balance_error:
            console.print(f"[bold red]Error getting balance: {str(balance_error)}[/bold red]")
            return
        
        # Get instrument info
        console.print(f"[cyan]Getting instrument info for {symbol}...[/cyan]")
        instrument_info = client.get_instruments_info(
            category="spot",
            symbol=symbol
        )
        
        # Print full instrument info for debugging
        console.print(f"[cyan]Full instrument info:[/cyan]")
        console.print(json.dumps(instrument_info["result"]["list"][0], indent=2))
        
        # Extract trading constraints
        lot_size_filter = instrument_info["result"]["list"][0]["lotSizeFilter"]
        price_filter = instrument_info["result"]["list"][0]["priceFilter"]
        min_order_qty = float(lot_size_filter["minOrderQty"])
        min_order_amt = float(lot_size_filter["minOrderAmt"])  # This is in USDT
        base_precision = lot_size_filter["basePrecision"]
        tick_size = float(price_filter["tickSize"])
        
        # Calculate decimal places for rounding
        decimal_places = len(base_precision.split('.')[-1]) if '.' in base_precision else 0
        price_decimal_places = len(str(tick_size).split('.')[-1]) if '.' in str(tick_size) else 0
        
        # Get current price
        ticker = client.get_tickers(category="spot", symbol=symbol)
        current_price = float(ticker["result"]["list"][0]["lastPrice"])
        
        console.print(f"[yellow]DOGE price: {current_price} USDT[/yellow]")
        console.print(f"[yellow]Minimum order quantity: {min_order_qty} DOGE[/yellow]")
        console.print(f"[yellow]Minimum order amount: {min_order_amt} USDT[/yellow]")
        
        # Try with a fixed amount similar to what worked in the web interface
        fixed_amount = 1.2  # USDT - similar to what worked in the web interface
        
        # Calculate quantity based on fixed amount
        calculated_qty = fixed_amount / current_price
        
        # Make sure it's at least minOrderQty
        if calculated_qty < min_order_qty:
            calculated_qty = min_order_qty
            
        # Round to appropriate decimal places
        qty_to_buy = round(calculated_qty, decimal_places)
        
        # Set limit price slightly below current price (0.5% lower)
        limit_price = current_price * 0.995
        
        # Make sure limit price is properly formatted with correct decimal places
        limit_price = round(limit_price, price_decimal_places)
        
        # Double check that limit price is not zero
        if limit_price <= 0:
            limit_price = current_price  # Use current price if calculation failed
            
        order_value = qty_to_buy * limit_price
        
        console.print(f"[yellow]Using fixed amount: {fixed_amount} USDT[/yellow]")
        console.print(f"[yellow]Limit order quantity: {qty_to_buy} DOGE[/yellow]")
        console.print(f"[yellow]Limit price: {limit_price} USDT[/yellow]")
        console.print(f"[yellow]Limit order value: {order_value:.2f} USDT[/yellow]")
        
        # Check if we have enough balance
        if usdt_balance < order_value:
            console.print(f"[bold red]Insufficient USDT balance. Need: {order_value:.2f} USDT, Have: {usdt_balance:.2f} USDT[/bold red]")
            return
            
        try:
            # Place limit order
            console.print(f"[cyan]Placing limit buy order for {qty_to_buy} DOGE at {limit_price} USDT...[/cyan]")
            order = client.place_order(
                category="spot",
                symbol=symbol,
                side="Buy",
                orderType="LIMIT",
                qty=str(qty_to_buy),
                price=str(limit_price),
                timeInForce="GTC"  # Good Till Cancelled
            )
            
            console.print(f"[bold green]✓ Limit buy order placed for {qty_to_buy} DOGE at {limit_price} USDT (Value: {order_value:.2f} USDT)[/bold green]")
            console.print(f"[bold green]Order details: {json.dumps(order['result'], indent=2)}[/bold green]")
            
            # Wait for order to be filled
            console.print(f"[cyan]Waiting for order to be filled...[/cyan]")
            order_id = order["result"]["orderId"]
            
            # Check order status every 5 seconds for up to 2 minutes
            for _ in range(24):  # 24 * 5 seconds = 2 minutes
                time.sleep(5)
                order_status = client.get_order_history(
                    category="spot",
                    symbol=symbol,
                    orderId=order_id
                )
                
                status = order_status["result"]["list"][0]["orderStatus"] if order_status["result"]["list"] else "Unknown"
                console.print(f"[cyan]Order status: {status}[/cyan]")
                
                if status == "Filled":
                    console.print(f"[bold green]✓ Order filled successfully![/bold green]")
                    break
                elif status in ["Cancelled", "Rejected", "Failed"]:
                    console.print(f"[bold red]Order {status}. Please check your Bybit account.[/bold red]")
                    break
            
            return
        except Exception as limit_order_error:
            console.print(f"[bold red]Error placing limit order: {str(limit_order_error)}[/bold red]")
            
            # Try with market order as a last resort
            console.print(f"[yellow]Trying with market order as a last resort...[/yellow]")
            try:
                order = client.place_order(
                    category="spot",
                    symbol=symbol,
                    side="Buy",
                    orderType="MARKET",
                    qty=str(qty_to_buy)
                )
                
                console.print(f"[bold green]✓ Market buy order placed for {qty_to_buy} DOGE (Value: ~{qty_to_buy * current_price:.2f} USDT)[/bold green]")
                console.print(f"[bold green]Order details: {json.dumps(order['result'], indent=2)}[/bold green]")
            except Exception as market_order_error:
                console.print(f"[bold red]Error placing market order: {str(market_order_error)}[/bold red]")
            
            return
        
    except Exception as e:
        console.print(f"[bold red]Error: {str(e)}[/bold red]")

if __name__ == "__main__":
    console.print("[bold]===== DOGE Buyer Script =====[/bold]")
    buy_doge()
    console.print("[bold]===== Script Completed =====[/bold]") 