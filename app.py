from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for
import json
import os
from datetime import datetime
from rich.console import Console
from rich.table import Table
import io
import requests
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = True
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Verileri saklamak için global değişkenler
latest_market_data = {}
last_update_time = None

# Rich konsol çıktılarını yakalamak için
console = Console(file=io.StringIO(), highlight=False)

# HTML template klasörü oluştur (başlangıçta)
def create_template_folder():
    os.makedirs('templates', exist_ok=True)
    
    # Basit bir index.html şablonu oluştur
    if not os.path.exists('templates/index.html'):
        with open('templates/index.html', 'w') as f:
            f.write("""
<!DOCTYPE html>
<html>
<head>
    <title>Market Analysis Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css" rel="stylesheet">
    <style>
        .buy { color: green; font-weight: bold; }
        .sell { color: red; font-weight: bold; }
        .neutral { color: gray; }
        .strong-buy { color: darkgreen; font-weight: bold; }
        
        th { position: sticky; top: 0; background-color: #f8f9fa; }
        
        .summary-box {
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
            text-align: center;
        }
        
        .buy-box { background-color: rgba(40, 167, 69, 0.2); }
        .sell-box { background-color: rgba(220, 53, 69, 0.2); }
        .neutral-box { background-color: rgba(108, 117, 125, 0.2); }
        .total-box { background-color: rgba(0, 123, 255, 0.2); }
        
        .indicator-value {
            font-size: 0.85em;
            display: block;
            color: #666;
        }
        
        .red-bg { background-color: rgba(255, 0, 0, 0.1); }
        .green-bg { background-color: rgba(0, 255, 0, 0.1); }
    </style>
</head>
<body>
    <div class="container-fluid">
        <h1 class="mt-4 mb-4">Market Analysis Dashboard</h1>
        <div class="d-flex justify-content-between align-items-center mb-3">
            <p class="mb-0">Last update: {{ last_update or 'No data yet' }}</p>
            <button class="btn btn-outline-primary" onclick="window.location.reload()">Yenile</button>
        </div>
        
        {% if market_data and market_data.summary %}
        <div class="row mb-4">
            <div class="col-md-3">
                <div class="summary-box buy-box">
                    <h4>Buy Signals</h4>
                    <h2>{{ market_data.summary.buy_signals }}</h2>
                </div>
            </div>
            <div class="col-md-3">
                <div class="summary-box sell-box">
                    <h4>Sell Signals</h4>
                    <h2>{{ market_data.summary.sell_signals }}</h2>
                </div>
            </div>
            <div class="col-md-3">
                <div class="summary-box neutral-box">
                    <h4>Neutral Signals</h4>
                    <h2>{{ market_data.summary.neutral_signals }}</h2>
                </div>
            </div>
            <div class="col-md-3">
                <div class="summary-box total-box">
                    <h4>Total Markets</h4>
                    <h2>{{ market_data.summary.total_markets }}</h2>
                </div>
            </div>
        </div>
        {% endif %}
        
        {% if market_data and market_data.market_data %}
        <!-- İndikatör isimlerini almak için yardımcı değişkenler tanımla -->
        {% set indicators = [] %}
        {% set first_market = none %}
        
        <!-- İlk geçerli market verisi bul -->
        {% for symbol, data in market_data.market_data.items() %}
            {% if data and data.signals and not first_market %}
                {% set first_market = data %}
                {% for indicator_name in data.signals.keys() %}
                    {% set indicators = indicators + [indicator_name] %}
                {% endfor %}
            {% endif %}
        {% endfor %}
        
        <!-- Verileri görüntüle -->
        <div class="table-responsive" style="max-height: 70vh;">
            <table class="table table-striped table-bordered table-hover" id="marketTable">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Price</th>
                        <th>24h Volume (M)</th>
                        
                        <!-- İndikatör sütunları -->
                        <th>RSI</th>
                        <th>SMA</th>
                        <th>MACD</th>
                        <th>BBANDS</th>
                        <th>FIBONACCI</th>
                        <th>Signal Strength</th>
                    </tr>
                </thead>
                <tbody>
                    {% for symbol, data in market_data.market_data.items() %}
                    <tr>
                        <td>{{ symbol }}</td>
                        <td>${{ "%.4f"|format(data.price) }}</td>
                        <td>${{ "%.2f"|format(data.volume) }}M</td>
                        
                        <!-- RSI -->
                        <td>
                            {% if data and data.signals and data.signals.RSI %}
                                <span class="
                                    {% if data.signals.RSI == 'BUY' or data.signals.RSI == 'STRONG_BUY' %}buy
                                    {% elif data.signals.RSI == 'SELL' or data.signals.RSI == 'STRONG_SELL' %}sell
                                    {% else %}neutral{% endif %}
                                ">
                                    {{ data.signals.RSI }}
                                </span>
                                
                                {% if data.values and data.values.RSI is defined %}
                                    <span class="indicator-value {% if data.values.RSI < 30 %}green-bg{% elif data.values.RSI > 70 %}red-bg{% endif %}">
                                        Value: {{ data.values.RSI }}
                                    </span>
                                {% endif %}
                            {% else %}
                                -
                            {% endif %}
                        </td>
                        
                        <!-- SMA -->
                        <td>
                            {% if data and data.signals and data.signals.SMA %}
                                <span class="
                                    {% if data.signals.SMA == 'BUY' or data.signals.SMA == 'STRONG_BUY' %}buy
                                    {% elif data.signals.SMA == 'SELL' or data.signals.SMA == 'STRONG_SELL' %}sell
                                    {% else %}neutral{% endif %}
                                ">
                                    {{ data.signals.SMA }}
                                </span>
                                
                                {% if data.values %}
                                    <span class="indicator-value">
                                        {% if data.values.SMA_Short %}Short: {{ data.values.SMA_Short }}<br>{% endif %}
                                        {% if data.values.SMA_Long %}Long: {{ data.values.SMA_Long }}<br>{% endif %}
                                        {% if data.values.SMA_Diff %}Diff: {{ data.values.SMA_Diff }}{% endif %}
                                    </span>
                                {% endif %}
                            {% else %}
                                -
                            {% endif %}
                        </td>
                        
                        <!-- MACD -->
                        <td>
                            {% if data and data.signals and data.signals.MACD %}
                                <span class="
                                    {% if data.signals.MACD == 'BUY' or data.signals.MACD == 'STRONG_BUY' %}buy
                                    {% elif data.signals.MACD == 'SELL' or data.signals.MACD == 'STRONG_SELL' %}sell
                                    {% else %}neutral{% endif %}
                                ">
                                    {{ data.signals.MACD }}
                                </span>
                                
                                {% if data.values %}
                                    <span class="indicator-value">
                                        {% if data.values.MACD_Line %}Line: {{ data.values.MACD_Line }}<br>{% endif %}
                                        {% if data.values.MACD_Signal %}Signal: {{ data.values.MACD_Signal }}<br>{% endif %}
                                        {% if data.values.MACD_Hist %}Hist: {{ data.values.MACD_Hist }}{% endif %}
                                    </span>
                                {% endif %}
                            {% else %}
                                -
                            {% endif %}
                        </td>
                        
                        <!-- BBANDS -->
                        <td>
                            {% if data and data.signals and data.signals.BBANDS %}
                                <span class="
                                    {% if data.signals.BBANDS == 'BUY' or data.signals.BBANDS == 'STRONG_BUY' %}buy
                                    {% elif data.signals.BBANDS == 'SELL' or data.signals.BBANDS == 'STRONG_SELL' %}sell
                                    {% else %}neutral{% endif %}
                                ">
                                    {{ data.signals.BBANDS }}
                                </span>
                                
                                {% if data.values %}
                                    <span class="indicator-value">
                                        {% if data.values.BB_Upper %}Upper: {{ data.values.BB_Upper }}<br>{% endif %}
                                        {% if data.values.BB_Middle %}Middle: {{ data.values.BB_Middle }}<br>{% endif %}
                                        {% if data.values.BB_Lower %}Lower: {{ data.values.BB_Lower }}<br>{% endif %}
                                        {% if data.values.BB_Width %}Width: {{ data.values.BB_Width }}{% endif %}
                                    </span>
                                {% endif %}
                            {% else %}
                                -
                            {% endif %}
                        </td>
                        
                        <!-- FIBONACCI -->
                        <td>
                            {% if data and data.signals and data.signals.FIBONACCI %}
                                <span class="
                                    {% if data.signals.FIBONACCI == 'BUY' or data.signals.FIBONACCI == 'STRONG_BUY' %}buy
                                    {% elif data.signals.FIBONACCI == 'SELL' or data.signals.FIBONACCI == 'STRONG_SELL' %}sell
                                    {% else %}neutral{% endif %}
                                ">
                                    {{ data.signals.FIBONACCI }}
                                </span>
                                
                                {% if data.values and data.values is mapping %}
                                    <span class="indicator-value">
                                        {% for key, value in data.values.items() %}
                                            {% if key.startswith('FIB_') %}
                                                {{ key }}: {{ value }}<br>
                                            {% endif %}
                                        {% endfor %}
                                    </span>
                                {% endif %}
                            {% else %}
                                -
                            {% endif %}
                        </td>
                        
                        <!-- Signal Strength - Önceden hesaplanmış değerleri kullan -->
                        <td data-order="{{ data.signal_strength.strength if data and data.signal_strength else 0 }}">
                            {% if data and data.signal_strength %}
                                {% set strength = data.signal_strength.strength %}
                                {% set rel_strength = data.signal_strength.rel_strength %}
                                
                                <span class="
                                    {% if strength > 0 %}buy
                                    {% elif strength < 0 %}sell
                                    {% else %}neutral{% endif %}
                                ">
                                    {{ strength }} ({{ rel_strength }})
                                </span>
                                
                                <!-- Görsel gösterge ekle -->
                                <div class="progress mt-1" style="height: 5px;">
                                    {% if strength > 0 %}
                                        <div class="progress-bar bg-success" role="progressbar" style="width: {{ strength * 10 }}%" aria-valuenow="{{ strength }}" aria-valuemin="0" aria-valuemax="10"></div>
                                    {% elif strength < 0 %}
                                        <div class="progress-bar bg-danger" role="progressbar" style="width: {{ strength * -10 }}%" aria-valuenow="{{ strength }}" aria-valuemin="-10" aria-valuemax="0"></div>
                                    {% else %}
                                        <div class="progress-bar bg-secondary" role="progressbar" style="width: 50%" aria-valuenow="0" aria-valuemin="-10" aria-valuemax="10"></div>
                                    {% endif %}
                                </div>
                            {% else %}
                                -
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% else %}
        <div class="alert alert-info">
            No market data available yet. Please run the analyzer.
        </div>
        {% endif %}
    </div>
    
    <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>
    <script>
        $(document).ready(function() {
            // DataTable'ı başlat
            const table = $('#marketTable').DataTable({
                paging: true,
                lengthMenu: [[10, 25, 50, 100, -1], [10, 25, 50, 100, "Tümü"]],
                pageLength: 25,
                order: [[8, 'desc']], // Sinyal gücüne göre sırala (8. sütun - 0'dan başlayarak)
                columnDefs: [{
                    // Sadece sayısal değerleri ayıklayarak sıralama yapacak özel sıralama fonksiyonu
                    targets: 8, // Sinyal gücü sütunu
                    type: 'num'
                }]
            });
        });
    </script>
</body>
</html>
            """)

# Uygulama başlamadan önce template klasörünü oluştur
create_template_folder()

@app.route('/')
def index():
    """Ana sayfa"""
    return render_template('index.html',
                          market_data=latest_market_data, 
                          last_update=last_update_time)

@app.route('/api/market-data', methods=['POST'])
def receive_market_data():
    """
    show_indicators.py'den gelen piyasa verilerini al
    
    Beklenen JSON formatı:
    {
        "market_data": {
            "BTCUSDT": {
                "price": 50000.0,
                "volume": 500.0,
                "signals": {
                    "RSI": "BUY",
                    "SMA": "SELL",
                    ...
                },
                "combined_signal": "NEUTRAL"
            },
            ...
        },
        "summary": {
            "buy_signals": 5,
            "sell_signals": 10,
            "neutral_signals": 20,
            "total_markets": 35
        }
    }
    """
    global latest_market_data, last_update_time
    
    if not request.is_json:
        return jsonify({"status": "error", "message": "Expected JSON data"}), 400
    
    data = request.get_json()
    
    # Gelen verileri kontrol et
    if not data or 'market_data' not in data:
        return jsonify({"status": "error", "message": "Invalid data format"}), 400
    
    # Sinyal gücü hesaplamasını burada yap
    for symbol, market_info in data['market_data'].items():
        if 'signals' in market_info:
            strength = 0
            for indicator, signal in market_info['signals'].items():
                if signal == "BUY":
                    strength += 1
                elif signal == "STRONG_BUY":
                    strength += 2
                elif signal == "SELL":
                    strength -= 1
                elif signal == "STRONG_SELL":
                    strength -= 2
            
            total_indicators = len(market_info['signals'])
            rel_strength = round(strength / total_indicators, 2) if total_indicators > 0 else 0
            
            # Hesaplanan değerleri market_info sözlüğüne ekle
            market_info['signal_strength'] = {
                'strength': strength,
                'rel_strength': rel_strength,
                'buy_count': sum(1 for s in market_info['signals'].values() if s in ["BUY", "STRONG_BUY"]),
                'sell_count': sum(1 for s in market_info['signals'].values() if s in ["SELL", "STRONG_SELL"]),
                'neutral_count': sum(1 for s in market_info['signals'].values() if s == "NEUTRAL")
            }
    
    # Verileri güncelle
    latest_market_data = data
    last_update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Verileri dosyaya kaydet (opsiyonel)
    with open('latest_market_data.json', 'w') as f:
        json.dump(data, f, indent=2)
    
    return jsonify({"status": "success", "message": "Data received successfully"})

@app.route('/api/market-data', methods=['GET'])
def get_market_data():
    """Son piyasa verilerini JSON formatında döndür"""
    global latest_market_data, last_update_time
    
    response = {
        "data": latest_market_data,
        "last_update": last_update_time
    }
    
    return jsonify(response)

@app.route('/generate-table')
def generate_table():
    """Rich kütüphanesini kullanarak tablo oluştur"""
    global latest_market_data
    
    if not latest_market_data or 'market_data' not in latest_market_data:
        return "No data available"
    
    # Rich ile tablo oluştur
    table = Table(title=f"Market Analysis - {last_update_time}")
    
    # Sütunları ekle
    table.add_column("Symbol", style="cyan")
    table.add_column("Price", justify="right", style="green")
    table.add_column("24h Volume", justify="right", style="yellow")
    
    # İndikatör sütunları için bir örnek veri bul
    sample_data = next(iter(latest_market_data['market_data'].values()), None)
    if sample_data and 'signals' in sample_data:
        for indicator in sample_data['signals']:
            table.add_column(indicator, justify="center")
    
    table.add_column("Signal", justify="center", style="bold")
    
    # Satırları ekle
    for symbol, data in latest_market_data['market_data'].items():
        row = [
            symbol,
            f"${data['price']:.4f}",
            f"${data['volume']:.2f}M"
        ]
        
        if 'signals' in data:
            for indicator, signal in data['signals'].items():
                color = "green" if signal in ['BUY', 'STRONG_BUY'] else "red" if signal == 'SELL' else "white"
                row.append(f"[{color}]{signal}[/{color}]")
        
        # Birleşik sinyal
        combined_signal = data.get('combined_signal', 'NEUTRAL')
        signal_color = "green" if combined_signal == 'BUY' else "red" if combined_signal == 'SELL' else "white"
        row.append(f"[{signal_color}]{combined_signal}[/{signal_color}]")
        
        table.add_row(*row)
    
    # Konsol için çıktıyı yakalamak için kullanılan StringIO'yu temizle
    console.file.seek(0)
    console.file.truncate(0)
    
    # Tabloyu konsola yazdır
    console.print(table)
    
    # Yakalanan çıktıyı al
    output = console.file.getvalue()
    
    return output

def calculate_indicator_strength(signals):
    """Göstergelerin sinyal gücünü hesapla"""
    strength = 0
    buy_count = 0
    sell_count = 0
    neutral_count = 0
    
    for signal in signals.values():
        if 'BUY' in signal:
            if 'STRONG' in signal:
                strength += 2
            else:
                strength += 1
            buy_count += 1
        elif 'SELL' in signal:
            if 'STRONG' in signal:
                strength -= 2
            else:
                strength -= 1
            sell_count += 1
        else:  # NEUTRAL veya diğer durumlar
            neutral_count += 1
    
    total = len(signals)
    rel_strength = round(strength / total, 2) if total > 0 else 0
    
    return {
        'strength': strength,
        'rel_strength': rel_strength,
        'buy_count': buy_count,
        'sell_count': sell_count,
        'neutral_count': neutral_count
    }

@app.route('/receive_data', methods=['POST'])
def receive_data():
    """Analiz edilmiş market verilerini al ve göster"""
    try:
        market_info = request.json
        
        # Piyasa verilerini hazırla ve JSON'dan Python nesnesine dönüştür
        if 'market_data' in market_info:
            # Her bir piyasa için sinyal gücünü hesapla
            for symbol, data in market_info['market_data'].items():
                if 'signals' in data:
                    data['signal_strength'] = calculate_indicator_strength(data['signals'])
        
        # Verileri global değişkene kaydet ve anında göstermeyi etkinleştir
        global market_data, last_update
        market_data = market_info
        last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({"status": "success", "message": "Data received successfully"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

@app.route('/chart/<symbol>')
def chart(symbol):
    # Market verilerini oku
    market_data = {}
    try:
        if os.path.exists('latest_market_data.json'):
            with open('latest_market_data.json', 'r') as f:
                market_data = json.load(f)
    except Exception as e:
        print(f"Veri okuma hatası: {e}")
    
    # Sembolü kontrol et
    if symbol not in market_data.get('market_data', {}):
        return redirect(url_for('index'))
    
    # Coin verilerini al
    coin_data = market_data.get('market_data', {}).get(symbol, {})
    
    # Config.json dosyasından kline ayarlarını oku
    interval = '15'  # Varsayılan değer
    limit = 365      # Varsayılan değer (1 yıl)
    
    try:
        if os.path.exists('config.json'):
            with open('config.json', 'r') as f:
                config = json.load(f)
                interval = config.get('trading', {}).get('kline', {}).get('interval', interval)
                config_limit = config.get('trading', {}).get('kline', {}).get('limit', 200)
                
                # Interval'e göre 1 yıllık veri için gereken limit hesabı
                if interval == 'D':
                    limit = 365  # Günlük veri için son 365 gün
                elif interval == 'W':
                    limit = 52   # Haftalık veri için son 52 hafta
                elif interval == 'M':
                    limit = 12   # Aylık veri için son 12 ay
                elif interval in ['60', '120', '240', '360', '720']:
                    # Saatlik ve üzeri periyotlar için 1 yıllık veri
                    # 1 gün = 24 saat
                    hours_in_day = 24
                    days_in_year = 365
                    total_hours = hours_in_day * days_in_year
                    hours_per_interval = int(interval) / 60
                    limit = min(int(total_hours / hours_per_interval), 1000)  # Makul bir limit
                elif interval == '30':
                    # 30 dakikalık periyot için 1 yıllık veri
                    # 1 gün = 48 adet 30dk
                    limit = min(48 * 365, 1000)  # Makul bir limit
                elif interval == '15':
                    # 15 dakikalık periyot için 1 yıllık veri
                    # 1 gün = 96 adet 15dk
                    limit = min(96 * 365, 1000)  # Makul bir limit
                else:
                    # Diğer kısa intervallar için
                    limit = min(config_limit, 1000)  # Makul bir limit
    except Exception as e:
        print(f"Config dosyası okuma hatası: {e}")
    
    # ByBit'ten geçmiş fiyat verilerini al
    historical_data = get_historical_data(symbol, interval, limit)
    
    return render_template('chart.html', 
                          symbol=symbol, 
                          coin_data=coin_data,
                          historical_data=historical_data,
                          interval=interval,
                          limit=limit,
                          last_update=market_data.get('last_update', ''))

def get_historical_data(symbol, interval=None, limit=None):
    """
    ByBit API'sinden geçmiş fiyat verilerini al
    
    Args:
        symbol: Coin sembolü (örn. BTCUSDT)
        interval: Zaman aralığı (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M)
        limit: Kaç veri noktası alınacak (max 200)
    
    Returns:
        Tarih, açılış, kapanış, yüksek, düşük ve hacim verilerini içeren liste
    """
    try:
        # Config.json'dan kline ayarlarını oku
        config = {}
        try:
            with open('config.json', 'r') as f:
                config = json.load(f)
        except Exception as e:
            print(f"Config dosyası okuma hatası: {e}")
        
        # Varsayılan değerleri config.json'dan al veya varsayılan değerleri kullan
        if interval is None:
            interval = config.get('trading', {}).get('kline', {}).get('interval', 'D')
        
        # Son 1 yıllık veri için ayarlar
        # ByBit API tek seferde maksimum 200 veri noktası döndürür
        # Yıllık veri için birkaç istek yapmamız gerekebilir
        if limit is None:
            if interval == 'D':
                limit = 365  # Günlük veri için son 365 gün
            elif interval == 'W':
                limit = 52   # Haftalık veri için son 52 hafta
            elif interval == 'M':
                limit = 12   # Aylık veri için son 12 ay
            elif interval in ['60', '120', '240', '360', '720']:
                # Saatlik ve üzeri periyotlar için API limiti nedeniyle 200 nokta alalım
                limit = 200
            elif interval == '30':
                # 30 dakikalık periyot için (günlük 48 nokta) - son ~4 ay
                limit = 200  
            elif interval == '15':
                # 15 dakikalık periyot için son ~2 ay
                limit = 200
            else:
                # Diğer kısa intervallar için API limiti
                limit = 200
        
        print(f"Tarihsel veri alınıyor - Symbol: {symbol}, Interval: {interval}, Requested Limit: {limit}")
        
        # ByBit API'si tek seferde maximum 200 veri noktası döndürür
        # Birden fazla istek yaparak daha fazla veri alabiliriz
        max_api_limit = 200
        result = []
        requests_needed = (limit + max_api_limit - 1) // max_api_limit  # Ceiling division
        
        print(f"Toplam {requests_needed} API isteği gerçekleştirilecek.")
        
        for i in range(requests_needed):
            # API URL
            url = f"https://api.bybit.com/v5/market/kline"
            
            # Her istekte en fazla 200 veri noktası alabiliriz
            current_limit = min(max_api_limit, limit - i * max_api_limit)
            if current_limit <= 0:
                break
                
            # Eğer ilk istek değilse, son alınan verinin zaman damgasını kullan
            end_time = None
            if i > 0 and result:
                # Son veriden bir saniye önceyi al
                last_timestamp = int(datetime.strptime(result[-1]["date"], '%Y-%m-%d %H:%M:%S').timestamp()) - 1
                end_time = last_timestamp * 1000  # milisaniye cinsinden
            
            # API parametreleri
            params = {
                "category": "spot",
                "symbol": symbol,
                "interval": interval,
                "limit": current_limit
            }
            
            # Eğer bir önceki istekten veri varsa, bitiş zamanını belirt
            if end_time:
                params["end"] = end_time
            
            print(f"API isteği #{i+1}: {url} - Parameters: {params}")
            
            # API isteği gönder
            try:
                response = requests.get(url, params=params, timeout=10)
                data = response.json()
                
                if data["retCode"] == 0 and "list" in data["result"]:
                    # API yanıtını işle
                    klines = data["result"]["list"]
                    
                    if not klines:
                        # Daha fazla veri yok
                        print(f"İstek #{i+1}: Veri bulunamadı.")
                        break
                    
                    print(f"İstek #{i+1}: {len(klines)} veri noktası alındı.")
                    
                    for kline in klines:
                        # Zamanı milisaniyeden normal zamana çevir
                        timestamp = int(kline[0])
                        date = datetime.fromtimestamp(timestamp/1000).strftime('%Y-%m-%d %H:%M:%S')
                        
                        result.append({
                            "date": date,
                            "open": float(kline[1]),
                            "high": float(kline[2]),
                            "low": float(kline[3]),
                            "close": float(kline[4]),
                            "volume": float(kline[5])
                        })
                    
                    # API rate limit aşımını önlemek için kısa bir bekleme
                    if i < requests_needed - 1:
                        time.sleep(0.5)
                else:
                    print(f"ByBit API hatası: {data}")
                    break
            except Exception as req_err:
                print(f"API isteği hatası: {req_err}")
                # Hataya rağmen devam et, belki diğer istekler başarılı olur
                time.sleep(1)
                continue
        
        # Sonuçları tarihe göre sırala (eskiden yeniye)
        result.sort(key=lambda x: x["date"])
        
        print(f"Toplam {len(result)} veri noktası alındı. İstenen limit: {limit}")
        
        # Sonuçları limit değerine göre kes
        if len(result) > limit:
            print(f"Sonuçlar {limit} veri noktasına kısıtlanıyor.")
            result = result[-limit:]
        
        return result
    except Exception as e:
        print(f"ByBit veri alımı hatası: {e}")
        return []

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
