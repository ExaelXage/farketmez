# Farketmez

Grup kararı uygulaması — nereye gidilecek, ne yenilecek artık farketmez.

## Kurulum

### 1. Bağımlılıkları yükle

```bash
pip install -r requirements.txt
```

### 2. Google Places API anahtarı al

Uygulama mekan araması için **Google Places API** kullanır. API anahtarı almak için:

1. [Google Cloud Console](https://console.cloud.google.com/) adresine git
2. Yeni proje oluştur (veya mevcut projeyi seç)
3. Sol menüden **APIs & Services → Library** seçeneğine git
4. **"Places API"** ara ve etkinleştir
5. **APIs & Services → Credentials** sayfasına git
6. **Create Credentials → API key** tıkla
7. Oluşturulan anahtarı kopyala

> **Not:** Google Places API ücretsiz kotası her ay 200 dolar kredi içerir (yaklaşık 5.000–10.000 ücretsiz istek). Kota aşımı için faturalandırma hesabı gerekir.

> **Güvenlik:** API anahtarını Google Cloud Console'dan kısıtla:
> - **Application restrictions:** HTTP referrers (yalnızca kendi domainin)
> - **API restrictions:** Places API

### 3. `.env` dosyasını düzenle

Proje kök dizinindeki `.env` dosyasını aç ve API anahtarını gir:

```env
SECRET_KEY=farketmez-dev-secret-2024
GOOGLE_PLACES_API_KEY=AIzaSy...buraya_anahtarini_yapistir
```

### 4. Uygulamayı başlat

```bash
python app.py
```

Tarayıcıda `http://localhost:5000` adresine git.

## Özellikler

- Oda oluştur, arkadaşları davet et
- Yemek veya etkinlik kategorisi seç
- GPS konumuna göre yakındaki mekanları listele (Google Places)
- Gerçek zamanlı grup oylaması (Socket.IO)
- Oy limiti: kullanıcı başına 3 oy
- Kazanan/kaybeden puan sistemi
- Alt kategori filtreleme (restoran, kafe, bar, pastane)

## Geliştirme

```
farketmez/
├── app.py           # Flask uygulaması + Socket.IO olayları
├── models.py        # SQLite veritabanı işlemleri
├── config.py        # Yapılandırma (API anahtarı, sabitler)
├── extensions.py    # Flask-SocketIO singleton
├── routes/
│   ├── api.py       # REST API (arama, oylama, sonuç)
│   └── room.py      # Sayfa rotaları
├── templates/       # Jinja2 HTML şablonları
└── static/
    ├── css/style.css
    └── js/app.js
```
