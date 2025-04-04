# Trading Bot Web Dashboard

Bu sistem, Bybit borsasında teknik analizler yapan ve sonuçları web arayüzünde gösteren bir yapıdır. İki ana bileşenden oluşur:

1. `show_indicators.py`: Bybit API'sini kullanarak yüksek hacimli kripto para çiftlerini analiz eder ve sonuçları bir web API'sine gönderir.
2. `app.py`: Analiz sonuçlarını alan ve güzel bir web arayüzünde gösteren Flask tabanlı bir web uygulaması.

## Kurulum

### Gereksinimler

```
pip install flask requests pandas numpy talib pybit python-dotenv rich
```

### Ayarlar

1. `.env` dosyasında Bybit API anahtarlarınızı ayarlayın:
```
BYBIT_API_KEY=your_api_key
BYBIT_API_SECRET=your_api_secret
```

2. `config.json` dosyasını indikatör ve alım-satım tercihleri doğrultusunda düzenleyin.

## Çalıştırma

Sistemi iki adımda başlatın:

### 1. Web sunucusunu başlatın

```bash
python app.py
```

Bu komut, Flask uygulamasını 5000 portunda başlatacaktır. Tarayıcınızda `http://localhost:5000` adresine giderek web arayüzüne ulaşabilirsiniz.

### 2. Analizörü başlatın

```bash
python show_indicators.py --api-url http://localhost:5000/api/market-data
```

Ek parametreler:
- `--testnet`: Bybit testnet'i kullanmak için (varsayılan olarak kapalı)
- `--config`: Farklı bir config dosyası belirtmek için (varsayılan: config.json)

## Web Arayüzü Özellikleri

Web arayüzü, analiz sonuçlarını canlı olarak gösterir ve 60 saniyede bir otomatik olarak yenilenir:

- Tüm piyasaların bir tablosu (Sembol, Fiyat, Hacim, İndikatör sinyalleri ve Birleşik sinyal)
- Alım ve satım sinyallerinin özeti
- Son güncelleme zamanı

## Sürekli Çalıştırma

Sistemi arka planda sürekli çalıştırmak için crontab veya bir sistem servisi oluşturabilirsiniz. Örnek crontab ayarı:

```
*/15 * * * * cd /path/to/project && python show_indicators.py --api-url http://localhost:5000/api/market-data >> indicator_log.txt 2>&1
```

Bu ayar, analizörü her 15 dakikada bir çalıştıracaktır.

## İndikatör ve Sinyal Mantığı

Sistem şu indikatörleri destekler:
- RSI (Relative Strength Index)
- SMA (Simple Moving Average)
- MACD (Moving Average Convergence Divergence)
- Bollinger Bands
- Fibonacci Retracement

Birleşik sinyal şu mantıkla hesaplanır:
- Tüm aktif indikatörler "ALIM" sinyali veriyorsa, birleşik sinyal "ALIM"
- Tüm aktif indikatörler "SATIM" sinyali veriyorsa, birleşik sinyal "SATIM"
- Aksi durumda "NÖTR"

## API Referansı

### POST /api/market-data

Analiz verilerini gönderir. JSON formatı:

```json
{
    "market_data": {
        "BTCUSDT": {
            "price": 50000.0,
            "volume": 500.0,
            "signals": {
                "RSI": "BUY",
                "SMA": "SELL",
                "MACD": "BUY",
                "BBANDS": "NEUTRAL",
                "FIBONACCI": "BUY"
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
```

### GET /api/market-data

Son piyasa verilerini JSON formatında döndürür.

## Not

Bu sistem, finansal tavsiye oluşturmak amacıyla değil, teknik analiz göstergelerini göstermek amacıyla oluşturulmuştur. Alım-satım kararları için profesyonel finansal danışmanlık alınması önerilir. 