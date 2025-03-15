import json
import time
from datetime import datetime
import requests
from rich.console import Console
from rich.table import Table

console = Console()

def simulate_order(category, symbol, side, order_type, qty):
    # Gerçek fiyatı Bybit'ten alalım
    try:
        price_response = requests.get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}")
        price_data = price_response.json()
        current_price = float(price_data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        console.print(f"[red]Error fetching price: {str(e)}[/red]")
        current_price = 0.0

    # Order ID oluştur (timestamp + random)
    order_id = f"simulator_{int(time.time() * 1000)}"
    
    # Toplam değeri hesapla
    value = float(qty) * current_price

    # Simüle edilmiş yanıt
    response = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "orderId": order_id,
            "orderLinkId": "",
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "price": str(current_price),
            "qty": qty,
            "timeInForce": "IOC",
            "orderStatus": "Filled",
            "execType": "Trade",
            "lastPriceOnCreated": str(current_price),
            "createdTime": str(int(time.time() * 1000)),
            "updatedTime": str(int(time.time() * 1000)),
            "isIsolated": False,
            "totalValue": f"{value:.8f}"
        },
        "retExtInfo": {},
        "time": int(time.time() * 1000)
    }

    # Tablo oluştur
    table = Table(title=f"Order Simulation - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Symbol", symbol)
    table.add_row("Side", side)
    table.add_row("Order Type", order_type)
    table.add_row("Quantity", str(qty))
    table.add_row("Price", f"{current_price:.8f}")
    table.add_row("Total Value", f"{value:.8f} USDT")
    table.add_row("Order ID", order_id)
    table.add_row("Status", "Filled")
    
    console.print(table)
    
    return response

if __name__ == "__main__":
    # Test order
    order_data = {
        "category": "spot",
        "symbol": "OIKUSDT",
        "side": "Buy",
        "orderType": "MARKET",
        "qty": "28.6"
    }
    
    console.print("\n[yellow]Simulating Order:[/yellow]")
    console.print(json.dumps(order_data, indent=2))
    console.print("\n[yellow]Response:[/yellow]")
    
    response = simulate_order(**order_data)
    
    console.print("\n[yellow]Raw Response JSON:[/yellow]")
    console.print(json.dumps(response, indent=2)) 