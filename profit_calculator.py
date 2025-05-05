import os
import json
import time
import math
import pandas as pd
from pybit.unified_trading import HTTP
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv()

class ProfitCalculator:
    def __init__(self, api_key, api_secret, investment_amount=10, profit_percentage=0.5, testnet=True):
        """
        Initialize the profit calculator
        
        Args:
            api_key: Bybit API key
            api_secret: Bybit API secret
            investment_amount: Amount in USDT to invest (default: 10 USDT)
            profit_percentage: Target profit percentage (default: 0.5%)
            testnet: Whether to use testnet (default: True)
        """
        self.client = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet
        )
        self.console = Console()
        self.investment_amount = investment_amount
        self.profit_percentage = profit_percentage
        self.fee_rate = 0.1  # 0.1% fee rate
        
    def get_min_order_size(self, symbol):
        """Get minimum order size and decimal precision for a symbol"""
        try:
            # Get instrument info
            instrument_info = self.client.get_instruments_info(
                category="spot",
                symbol=symbol
            )
            
            if instrument_info.get("retCode") != 0 or not instrument_info.get("result", {}).get("list"):
                self.console.print(f"[bold red]Error getting instrument info for {symbol}: {instrument_info.get('retMsg')}[/bold red]")
                return None, None, None
            
            # Extract values from response
            lot_size_filter = instrument_info["result"]["list"][0]["lotSizeFilter"]
            min_qty = float(lot_size_filter["minOrderQty"])
            min_order_amt = float(lot_size_filter["minOrderAmt"])
            
            # Calculate decimal places from basePrecision
            base_precision = lot_size_filter["basePrecision"]
            decimal_places = len(base_precision.split(".")[1]) if "." in base_precision else 0
            
            return min_qty, min_order_amt, decimal_places
            
        except Exception as e:
            self.console.print(f"[bold red]Error getting min order size for {symbol}: {str(e)}[/bold red]")
            return None, None, None
    
    def get_tickers(self):
        """Get all available tickers from Bybit"""
        try:
            tickers = self.client.get_tickers(category="spot")
            if tickers.get("retCode") == 0 and tickers.get("result", {}).get("list"):
                return tickers["result"]["list"]
            else:
                self.console.print(f"[bold red]Error getting tickers: {tickers.get('retMsg')}[/bold red]")
                return []
        except Exception as e:
            self.console.print(f"[bold red]Error getting tickers: {str(e)}[/bold red]")
            return []
    
    def filter_usdt_pairs(self, tickers):
        """Filter tickers to only include USDT pairs"""
        usdt_pairs = []
        for ticker in tickers:
            symbol = ticker.get("symbol", "")
            if symbol.endswith("USDT") and not symbol.startswith("USDT"):
                usdt_pairs.append(ticker)
        return usdt_pairs
    
    def calculate_profit(self, ticker):
        """Calculate potential profit for a given ticker"""
        symbol = ticker.get("symbol", "")
        last_price = float(ticker.get("lastPrice", 0))
        
        if last_price <= 0:
            return None
        
        # Get minimum order size and decimal precision
        min_qty, min_order_amt, decimal_places = self.get_min_order_size(symbol)
        if min_qty is None:
            return None
        
        # 1. Calculate how many coins can be bought with investment amount
        gross_coin_amount = self.investment_amount / last_price
        
        # Check if the amount meets minimum order requirements
        if gross_coin_amount < min_qty or self.investment_amount < min_order_amt:
            return None
        
        # 2. Deduct fee from coin amount (0.1% fee)
        fee_in_coins = gross_coin_amount * (self.fee_rate / 100)
        net_coin_amount = gross_coin_amount - fee_in_coins
        
        # Round to correct decimal places
        if decimal_places is not None:
            multiplier = 10 ** decimal_places
            net_coin_amount = math.floor(net_coin_amount * multiplier) / multiplier
        
        # 3. Calculate target price with profit percentage
        target_price = last_price * (1 + (self.profit_percentage / 100))
        
        # 4. Calculate gross sell value
        gross_sell_value = net_coin_amount * target_price
        
        # 5. Deduct sell fee (0.1%)
        sell_fee = gross_sell_value * (self.fee_rate / 100)
        net_sell_value = gross_sell_value - sell_fee
        
        # 6. Calculate profit
        profit = net_sell_value - self.investment_amount
        profit_percentage = (profit / self.investment_amount) * 100
        
        return {
            "symbol": symbol,
            "current_price": last_price,
            "target_price": target_price,
            "investment": self.investment_amount,
            "gross_coin_amount": gross_coin_amount,
            "fee_in_coins": fee_in_coins,
            "net_coin_amount": net_coin_amount,
            "gross_sell_value": gross_sell_value,
            "sell_fee": sell_fee,
            "net_sell_value": net_sell_value,
            "profit": profit,
            "profit_percentage": profit_percentage,
            "min_qty": min_qty,
            "min_order_amt": min_order_amt,
            "decimal_places": decimal_places
        }
    
    def display_results(self, results):
        """Display profit calculation results in a table"""
        table = Table(title=f"Profit Calculation (Investment: {self.investment_amount} USDT, Target: +{self.profit_percentage}%)")
        
        # Add columns
        table.add_column("Symbol", style="cyan")
        table.add_column("Current Price", style="green")
        table.add_column("Target Price", style="yellow")
        table.add_column("Net Coins", style="blue")
        table.add_column("Decimal Places", style="blue")
        table.add_column("Min Qty", style="yellow")
        table.add_column("Min Order", style="yellow")
        table.add_column("Net Profit", style="magenta")
        table.add_column("Profit %", style="bright_green")
        
        # Sort results by profit percentage (descending)
        sorted_results = sorted(results, key=lambda x: x["profit_percentage"], reverse=True)
        
        # Add rows
        for result in sorted_results:
            table.add_row(
                result["symbol"],
                f"{result['current_price']:.8f}",
                f"{result['target_price']:.8f}",
                f"{result['net_coin_amount']:.8f}",
                f"{result['decimal_places']}",
                f"{result['min_qty']:.8f}",
                f"{result['min_order_amt']:.2f} USDT",
                f"{result['profit']:.8f}",
                f"{result['profit_percentage']:.4f}%"
            )
        
        self.console.print(table)
        
        # Print summary
        self.console.print(f"\n[bold green]Summary:[/bold green]")
        self.console.print(f"Investment amount: {self.investment_amount} USDT")
        self.console.print(f"Target profit percentage: {self.profit_percentage}%")
        self.console.print(f"Fee rate: {self.fee_rate}%")
        self.console.print(f"Total pairs analyzed: {len(results)}")
        
        # Print timestamp
        self.console.print(f"\n[dim]Calculation performed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
    
    def run(self):
        """Run the profit calculator"""
        self.console.print("[bold]Fetching tickers from Bybit...[/bold]")
        tickers = self.get_tickers()
        
        if not tickers:
            self.console.print("[bold red]No tickers found. Exiting.[/bold red]")
            return
        
        self.console.print(f"[green]Found {len(tickers)} tickers[/green]")
        
        # Filter USDT pairs
        usdt_pairs = self.filter_usdt_pairs(tickers)
        self.console.print(f"[green]Filtered to {len(usdt_pairs)} USDT pairs[/green]")
        
        # Define popular coins to analyze
        popular_symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", 
                          "DOGEUSDT", "AVAXUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT"]
        
        # Find the tickers for popular coins
        popular_tickers = []
        for ticker in usdt_pairs:
            symbol = ticker.get("symbol", "")
            if symbol in popular_symbols:
                popular_tickers.append(ticker)
        
        self.console.print(f"[green]Analyzing {len(popular_tickers)} popular coins...[/green]")
        
        # Calculate profit for popular pairs
        results = []
        for ticker in popular_tickers:
            self.console.print(f"[dim]Processing {ticker.get('symbol', '')}...[/dim]")
            result = self.calculate_profit(ticker)
            if result:
                results.append(result)
        
        # Display results
        if results:
            self.display_results(results)
        else:
            self.console.print("[bold red]No valid results found.[/bold red]")
            
        # Ask if user wants to analyze a specific coin
        self.console.print("\n[bold]Would you like to analyze a specific coin? Enter symbol (e.g., BTCUSDT) or 'q' to quit:[/bold]")
        symbol = input()
        
        while symbol.lower() != 'q':
            found = False
            for ticker in usdt_pairs:
                if ticker.get("symbol", "") == symbol:
                    result = self.calculate_profit(ticker)
                    if result:
                        # Display single result
                        single_results = [result]
                        self.display_results(single_results)
                    else:
                        self.console.print(f"[bold red]Could not calculate profit for {symbol}.[/bold red]")
                    found = True
                    break
            
            if not found:
                self.console.print(f"[bold red]Symbol {symbol} not found.[/bold red]")
            
            self.console.print("\n[bold]Enter another symbol to analyze or 'q' to quit:[/bold]")
            symbol = input()

if __name__ == "__main__":
    # Get API credentials from environment variables
    api_key = os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET")
    
    if not api_key or not api_secret:
        Console().print("[bold red]API credentials not found in environment variables. Please set BYBIT_API_KEY and BYBIT_API_SECRET.[/bold red]")
        exit(1)
    
    # Create and run profit calculator
    calculator = ProfitCalculator(
        api_key=api_key,
        api_secret=api_secret,
        investment_amount=63210,  # 20 USDT
        profit_percentage=0.5,  # 0.5% profit target
        testnet=False  # Using real market data
    )
    
    calculator.run()
