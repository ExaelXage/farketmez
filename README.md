# Farketmez

Grup karar verme uygulaması. Oda oluştur, arkadaşlarını davet et, yakındaki mekanları birlikte oylayın.

**Stack:** Python 3.12 · Flask · Flask-SocketIO · SQLite · Overpass API · Foursquare Places API

```
farketmez/
├── app.py           # Flask + Socket.IO olayları
├── models.py        # SQLite işlemleri
├── config.py        # Yapılandırma sabitleri
├── extensions.py    # SocketIO singleton
├── routes/
│   ├── api.py       # REST API (JSON)
│   └── room.py      # Sayfa rotaları + admin
├── templates/       # Jinja2 şablonları
└── static/
    ├── css/style.css
    ├── js/app.js
    └── img/         # logo.svg, favicon.svg, favicon.ico
```

---

## Yerel Kurulum

```bash
pip install -r requirements.txt
# .env dosyası oluştur:
# SECRET_KEY=...
# FOURSQUARE_API_KEY=...
python app.py
# → http://localhost:5000
```

## Deployment (Render.com)

`render.yaml` ile otomatik deploy. Gerekli env değişkenleri:

| Değişken | Açıklama |
|----------|----------|
| `SECRET_KEY` | Flask session anahtarı |
| `FOURSQUARE_API_KEY` | Foursquare Places API anahtarı |
| `SOCKETIO_ASYNC_MODE` | `gevent` (production) |
| `ADMIN_PASSWORD` | Admin panel şifresi (varsayılan: `farketmez2024`) |

---

## Flutter / Mobil Entegrasyonu — API Dokümantasyonu

### Base URL

```
https://<your-render-domain>.onrender.com   # production
http://192.168.x.x:5000                     # local network
```

### Kimlik Doğrulama

Tüm API **session cookie** kullanır. Flutter'da cookie jar destekli bir HTTP istemcisi kullan.

**Önerilen paketler:** [`dio`](https://pub.dev/packages/dio) + [`dio_cookie_manager`](https://pub.dev/packages/dio_cookie_manager) + [`cookie_jar`](https://pub.dev/packages/cookie_jar)

```dart
final cookieJar = CookieJar();
final dio = Dio()..interceptors.add(CookieManager(cookieJar));
```

**Akış:**
1. `POST /create` veya `POST /room/<code>/join` → sunucu `Set-Cookie: session=...` döner
2. Sonraki tüm isteklerde dio otomatik olarak cookie'yi gönderir

---

## Endpoint'ler

### 1. Oda Oluştur

```
POST /create
Content-Type: application/x-www-form-urlencoded
```

**Request body:**

| Alan | Tip | Zorunlu | Açıklama |
|------|-----|---------|----------|
| `nickname` | string | ✓ | Takma ad (max 32 karakter) |
| `category` | string | ✓ | `"food"` veya `"activity"` |

**Response:** `302 Redirect → /room/<CODE>`

Yönlendirme URL'sinden oda kodunu parse et:

```dart
final res = await dio.post('/create',
  data: 'nickname=ali&category=food',
  options: Options(
    contentType: 'application/x-www-form-urlencoded',
    followRedirects: false,
    validateStatus: (s) => s! < 400,
  ),
);
// res.headers['location'] → ['/room/D34C9C06']
final roomCode = res.headers['location']!.first.split('/').last;
```

---

### 2. Odaya Katıl

```
POST /room/<code>/join
Content-Type: application/x-www-form-urlencoded
```

**Path:** `code` — 8 karakterli oda kodu (örn. `D34C9C06`)

**Request body:**

| Alan | Tip | Zorunlu | Açıklama |
|------|-----|---------|----------|
| `nickname` | string | ✓ | Takma ad (max 32 karakter) |

**Response:** `302 Redirect → /room/<code>`

```dart
await dio.post('/room/$code/join',
  data: 'nickname=veli',
  options: Options(
    contentType: 'application/x-www-form-urlencoded',
    followRedirects: false,
    validateStatus: (s) => s! < 400,
  ),
);
// Cookie otomatik set edilir, artık API istekleri yapılabilir
```

---

### 3. Oda Durumu

```
GET /api/room/<code>/status
```

**Auth:** Gerekmiyor

**Response `200`:**
```json
{
  "status": "waiting",
  "participants": [
    {
      "id": 1,
      "room_id": 1,
      "nickname": "ali",
      "token": "8f3a...",
      "score": 0,
      "is_owner": 1,
      "joined_at": "2026-05-20T19:15:03"
    }
  ],
  "summary": []
}
```

**`status` değerleri:**

| Değer | Açıklama |
|-------|----------|
| `waiting` | Konum bekleniyor, mekan araması yapılmadı |
| `voting` | Aktif oylama sürüyor |
| `completed` | Oylama bitti, sonuçlar hazır |

`summary` alanı `voting`/`completed` durumunda Place nesneleri içerir.

---

### 4. Mekan Ara

```
POST /api/room/<code>/search
Content-Type: application/json
```

**Auth:** Gerekli

**Request body:**
```json
{
  "lat": 40.1826,
  "lng": 29.0665,
  "radius": 2000
}
```

| Alan | Tip | Zorunlu | Açıklama |
|------|-----|---------|----------|
| `lat` | float | ✓ | Enlem |
| `lng` | float | ✓ | Boylam |
| `radius` | integer | — | Metre: `500`, `1000`, `2000` (varsayılan), `5000` |

> Yeterli mekan bulunamazsa (`< 15`) radius otomatik 5 km → 10 km → 20 km genişler.
> `actual_radius` gerçekte kullanılan değeri döner.

**Response `200`:**
```json
{
  "places": [
    {
      "id": 42,
      "room_id": 1,
      "osm_id": "123456789",
      "name": "Burger House",
      "category": "restaurant",
      "lat": 40.1831,
      "lng": 29.0671,
      "address": "Atatürk Cd. 12 Bursa",
      "total_score": 0,
      "likes": 0,
      "dislikes": 0
    }
  ],
  "actual_radius": 2000
}
```

**Hatalar:**

| Kod | Açıklama |
|-----|----------|
| `400` | `lat` veya `lng` eksik |
| `403` | Geçersiz oturum / odaya ait değil |
| `404` | Oda bulunamadı |
| `502` | Overpass API hatası |

> **Socket.IO:** Bu endpoint başarıyla tamamlanınca `places_loaded` eventi yayılır.

---

### 5. Oy Ver

```
POST /api/room/<code>/vote
Content-Type: application/json
```

**Auth:** Gerekli

**Request body:**
```json
{
  "place_id": 42,
  "value": 1
}
```

| Alan | Tip | Açıklama |
|------|-----|----------|
| `place_id` | integer | Oylanacak mekan ID'si |
| `value` | integer | `1` (beğen) veya `-1` (beğenme) |

**Sınırlamalar:**
- Kullanıcı başına maksimum **3 oy**
- Aynı mekana ikinci oy kullanılamaz

**Response `200`:**
```json
{
  "ok": true,
  "summary": [ /* güncel Place nesneleri, skor sıralı */ ]
}
```

**Hatalar:**

| Kod | Açıklama |
|-----|----------|
| `400` | Geçersiz `value` veya oy hakkı doldu |
| `403` | Yetkisiz |
| `404` | Oda bulunamadı |
| `409` | Bu mekana zaten oy verildi |

> **Socket.IO:** `vote_update` eventi tüm odaya yayılır.

---

### 6. Oylamayı Bitir

```
POST /api/room/<code>/finish
Content-Type: application/json
```

**Auth:** Gerekli · **Yalnızca oda sahibi**

**Request body:** `{}`

**Response `200`:**
```json
{
  "ok": true,
  "winner": { /* kazanan Place nesnesi */ },
  "summary": [ /* tüm mekanlar, skor sıralı */ ],
  "participants": [ /* güncel katılımcılar */ ],
  "score_log": [
    {
      "participant_id": 1,
      "nickname": "ali",
      "delta": 30,
      "breakdown": [
        "+5 katılım bonusu",
        "+25 kazanana oy verdi"
      ]
    }
  ]
}
```

**Hatalar:**

| Kod | Açıklama |
|-----|----------|
| `403` | Yetkisiz veya oda sahibi değil |
| `404` | Oda bulunamadı |

> **Socket.IO:** `show_results` eventi tüm odaya yayılır.

---

### 7. Sonuçları Getir (Polling)

```
GET /api/room/<code>/results
```

**Auth:** Gerekmiyor

Socket.IO bağlantısı kesilirse bu endpoint'i 3 saniyede bir poll et.

**Response `200` — oylama devam ediyor:**
```json
{ "completed": false }
```

**Response `200` — oylama bitti:**
```json
{
  "completed": true,
  "winner": { /* Place nesnesi */ },
  "summary": [ /* Place nesneleri, skor sıralı */ ],
  "participants": [ /* Participant nesneleri */ ]
}
```

---

### 8. İstatistikler

```
GET /api/stats
```

**Auth:** Gerekmiyor

**Response `200`:**
```json
{
  "total_rooms": 42,
  "active_rooms": 5,
  "total_users": 187,
  "total_votes": 634
}
```

---

## Veri Modelleri

### Place (Mekan)

| Alan | Tip | Açıklama |
|------|-----|----------|
| `id` | integer | Veritabanı ID |
| `room_id` | integer | Ait olduğu oda ID |
| `osm_id` | string | OpenStreetMap ID veya `fsq_<id>` (Foursquare) |
| `name` | string | Mekan adı |
| `category` | string | `restaurant`, `cafe`, `park`, `cinema`, vb. |
| `lat` | float | Enlem |
| `lng` | float | Boylam |
| `address` | string | Adres (`"—"` bilinmiyorsa) |
| `total_score` | integer | `likes - dislikes` |
| `likes` | integer | Toplam beğeni |
| `dislikes` | integer | Toplam beğenmeme |

### Participant (Katılımcı)

| Alan | Tip | Açıklama |
|------|-----|----------|
| `id` | integer | Veritabanı ID |
| `room_id` | integer | Ait olduğu oda ID |
| `nickname` | string | Takma ad |
| `token` | string | Oturum token'ı (gizli tut) |
| `score` | integer | Oyun puanı |
| `is_owner` | integer | `1` = oda sahibi, `0` = katılımcı |
| `joined_at` | string | ISO 8601 timestamp |

---

## Socket.IO

**Paket (Flutter):** [`socket_io_client`](https://pub.dev/packages/socket_io_client)

```dart
import 'package:socket_io_client/socket_io_client.dart' as IO;

final socket = IO.io('https://<domain>', IO.OptionBuilder()
  .setTransports(['websocket'])
  .enableAutoConnect()
  .build());

socket.onConnect((_) {
  socket.emit('join_room_ws', {'code': roomCode});
});
```

### Sunucudan Gelen Eventler

| Event | Ne zaman | Payload alanları |
|-------|----------|-----------------|
| `participants_update` | Odaya birisi katılınca | `participants: [...]` |
| `places_loaded` | Mekan araması bitince | `places`, `lat`, `lng`, `actual_radius` |
| `vote_update` | Oy kullanılınca | `summary`, `participants` |
| `show_results` | Oylama bitince | `winner`, `summary`, `participants`, `score_log` |

### İstemciden Gönderilen Eventler

| Event | Payload | Açıklama |
|-------|---------|----------|
| `join_room_ws` | `{"code": "D34C9C06"}` | Odanın Socket.IO kanalına abone ol |

---

## Puan Sistemi

| Olay | Puan |
|------|------|
| Her oy kullanımı | +2 |
| Katılım bonusu (oylama bitince) | +5 |
| Oy kullanmama cezası | −5 |
| Kazanan mekana oy verdiysen | +25 |
| Kazanan mekana karşı oy verdiysen | −20 |
| Kaybeden mekana beğeni verdiysen | −5 |

---

## Hata Formatı

Tüm hata yanıtları:
```json
{ "error": "Hata açıklaması" }
```
