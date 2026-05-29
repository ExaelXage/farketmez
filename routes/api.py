from flask import Blueprint, request, jsonify, session
import requests
import models
from config import Config
from extensions import socketio
from concurrent.futures import ThreadPoolExecutor, as_completed

bp = Blueprint("api", __name__, url_prefix="/api")


def _current_participant(room_id=None):
    import json as _json

    content_type = request.content_type or ""
    raw_body     = request.get_data(as_text=True)  # her zaman cache'ten gelir, tekrar okunabilir
    print(f"[AUTH] content-type={content_type!r}  body_len={len(raw_body)}  raw={raw_body[:120]!r}")

    # 1) Custom header
    token = request.headers.get("X-Participant-Token")

    # 2) JSON body — get_data() ile ham body oku, elle parse et (force/silent güvenilmez)
    if not token and raw_body:
        try:
            body  = _json.loads(raw_body)
            token = body.get("token")
            if token:
                print(f"[AUTH] token body'den alındı: {token[:8]}...")
        except Exception as exc:
            print(f"[AUTH] body parse hatası: {exc}")

    # 3) Session cookie (web tarayıcı)
    if not token:
        token = session.get("token")

    if not token:
        print("[AUTH] token bulunamadı — header, body ve session hepsi boş")
        return None

    p = models.get_participant_by_token(token)
    if p is None:
        print(f"[AUTH] token DB'de yok: {token[:8]}...")
        return None
    if room_id and p["room_id"] != room_id:
        print(f"[AUTH] oda uyuşmuyor: token.room={p['room_id']} beklenen={room_id}")
        return None
    return p


# ── Mobil / Flutter JSON endpoint'leri ─────────────────────────────────────

@bp.route("/rooms", methods=["POST"])
def create_room_json():
    data     = request.get_json(force=True) or {}
    nickname = data.get("nickname", "").strip()
    category = data.get("category", "food")
    lat      = data.get("lat")
    lng      = data.get("lng")

    if not nickname:
        return jsonify({"error": "Takma ad gerekli"}), 400

    code = models.create_room(nickname, category)
    room = models.get_room(code)

    if lat is not None and lng is not None:
        models.update_room_location(code, lat, lng)

    token = models.join_room(room["id"], nickname, is_owner=True)
    participants = [dict(p) for p in models.get_room_participants(room["id"])]

    return jsonify({
        "code":        code,
        "token":       token,
        "category":    category,
        "status":      "waiting",
        "participants": participants,
    }), 201


@bp.route("/rooms/<code>/join", methods=["POST"])
def join_room_json(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404
    if room["status"] == "completed":
        return jsonify({"error": "Oylama tamamlandı"}), 400

    data     = request.get_json(force=True) or {}
    nickname = data.get("nickname", "").strip()
    if not nickname:
        return jsonify({"error": "Takma ad gerekli"}), 400

    token        = models.join_room(room["id"], nickname, is_owner=False)
    participants = [dict(p) for p in models.get_room_participants(room["id"])]

    socketio.emit("participants_update", {"participants": participants}, to=code)

    return jsonify({
        "code":         code,
        "token":        token,
        "category":     room["category"],
        "status":       room["status"],
        "participants": participants,
    }), 200


@bp.route("/rooms/<code>", methods=["GET"])
def get_room_json(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404
    participants = [dict(p) for p in models.get_room_participants(room["id"])]
    return jsonify({
        "code":         code,
        "category":     room["category"],
        "status":       room["status"],
        "participants": participants,
    })


# ── Google Places (New API) ──────────────────────────────────────────────────

_GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"

_GOOGLE_TYPES = {
    "food":     ["restaurant", "cafe", "bakery", "bar"],
    "activity": ["park", "museum", "movie_theater"],
}


# ── Filtreler ────────────────────────────────────────────────────────────────

# Zincir market isimleri — başlangıç eşleşmesi (büyük/küçük harf yok sayılır)
_BLOCKED_CHAIN_NAMES = (
    "bim", "a101", "şok", "sok", "migros", "carrefoursa", "carrefour sa",
)

# Foursquare / Google category adında geçmesi yeterli anahtar kelimeler
_BLOCKED_KEYWORDS = (
    "supermarket", "convenience store", "grocery store", "market",
)


def _is_blocked_name(name: str) -> bool:
    n = name.lower().strip()
    for chain in _BLOCKED_CHAIN_NAMES:
        if n == chain or n.startswith(chain + " "):
            return True
    return False


def _is_blocked_fsq(cat_names: list) -> bool:
    for cat in cat_names:
        cat_lower = cat.lower()
        if any(kw in cat_lower for kw in _BLOCKED_KEYWORDS):
            return True
    return False


# ── Foursquare sorguları ─────────────────────────────────────────────────────

_FOURSQUARE_CATEGORY_MAP = {
    "food":     "13065,13032,13145,13002",  # restaurant, cafe, fast food, bakery
    "activity": "10000",
}


def _fetch_foursquare(lat, lng, radius, category):
    api_key = Config.FOURSQUARE_API_KEY
    if not api_key:
        return []

    params = {
        "ll":               f"{lat},{lng}",
        "radius":           radius,
        "fsq_category_ids": _FOURSQUARE_CATEGORY_MAP.get(category, "13065,13032,13145,13002"),
        "limit":            50,
        "fields":           "fsq_id,name,categories,location,geocodes",
    }
    headers = {
        "Accept":               "application/json",
        "Authorization":        f"Bearer {api_key}",
        "X-Places-Api-Version": "2025-06-17",
    }

    try:
        resp = requests.get(
            Config.FOURSQUARE_API_URL,
            params=params,
            headers=headers,
            timeout=(8, 12),  # (connect, read) — body bazen gelmiyor
            stream=True,
        )
        if resp.status_code != 200:
            return []
        results = resp.json().get("results", [])
    except Exception:
        return []

    places = []
    for r in results:
        name = (r.get("name") or "").strip()
        if not name:
            continue

        cats      = r.get("categories", [])
        cat_names = [c.get("name", "") for c in cats]

        if _is_blocked_name(name) or _is_blocked_fsq(cat_names):
            continue

        geo  = r.get("geocodes", {}).get("main", {})
        rlat = geo.get("latitude")
        rlng = geo.get("longitude")
        if rlat is None or rlng is None:
            continue

        loc   = r.get("location", {})
        parts = [loc.get("address", ""), loc.get("locality", "")]
        addr  = ", ".join(p for p in parts if p) or "—"

        cat_name = cat_names[0] if cat_names else category

        places.append({
            "osm_id":   f"fsq_{r.get('fsq_id', '')}",
            "name":     name,
            "category": cat_name,
            "lat":      rlat,
            "lng":      rlng,
            "address":  addr,
        })

    return places


# ── Google Places ────────────────────────────────────────────────────────────

_GOOGLE_SKIP_TYPES = frozenset({"point_of_interest", "establishment", "food", "lodging"})


def _parse_google_results(raw_places, category):
    places = []
    for r in raw_places:
        name = (r.get("displayName", {}).get("text") or "").strip()
        if not name or _is_blocked_name(name):
            continue
        loc  = r.get("location", {})
        rlat = loc.get("latitude")
        rlng = loc.get("longitude")
        if rlat is None or rlng is None:
            continue
        rtypes   = r.get("types", [])
        cat_name = next((t for t in rtypes if t not in _GOOGLE_SKIP_TYPES), category)
        places.append({
            "osm_id":   f"gp_{r['id']}",
            "name":     name,
            "category": cat_name,
            "lat":      rlat,
            "lng":      rlng,
            "address":  r.get("formattedAddress") or "—",
        })
    return places


def _fetch_google_places(lat, lng, radius, category):
    api_key = Config.GOOGLE_PLACES_API_KEY
    if not api_key:
        return [], "Google Places API key eksik"

    try:
        resp = requests.post(
            _GOOGLE_PLACES_URL,
            json={
                "includedTypes": _GOOGLE_TYPES.get(category, _GOOGLE_TYPES["food"]),
                "maxResultCount": 20,
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lng},
                        "radius": float(min(radius, 50000)),
                    }
                },
                "languageCode": "tr",
            },
            headers={
                "X-Goog-Api-Key":   api_key,
                "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.types",
                "Content-Type":     "application/json",
            },
            timeout=10,
        )
    except requests.exceptions.Timeout:
        print("[Google Places] timeout (10s)")
        return [], "Google Places zaman aşımı"
    except Exception as e:
        print(f"[Google Places] istek hatası: {e}")
        return [], f"Google Places bağlantı hatası: {e}"

    if resp.status_code != 200:
        msg = resp.json().get("error", {}).get("message", resp.text[:120])
        print(f"[Google Places] HTTP {resp.status_code}: {msg}")
        return [], f"Google Places HTTP {resp.status_code}"

    raw    = resp.json().get("places", [])
    places = _parse_google_results(raw, category)
    print(f"[Google Places] radius={radius}m cat={category} -> {len(places)} mekan")
    return places, None


# ── Konum & Mekan arama ─────────────────────────────────────────────────────

_MIN_PLACES    = 15
_EXPAND_RADII  = [5_000, 10_000, 20_000]  # otomatik genişleme adımları


def _combined_search(lat, lng, radius, category):
    """Google Places + Foursquare paralel çalıştır, sonuçları birleştir."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_google = pool.submit(_fetch_google_places, lat, lng, radius, category)
        fut_fsq    = pool.submit(_fetch_foursquare,    lat, lng, radius, category)
        google_places, google_error = fut_google.result()
        fsq_places                  = fut_fsq.result()

    seen_names = {p["name"].lower() for p in google_places}
    added_fsq  = 0
    for fp in fsq_places:
        if fp["name"].lower() not in seen_names:
            google_places.append(fp)
            seen_names.add(fp["name"].lower())
            added_fsq += 1

    print(f"[Search] radius={radius}m -> Google:{len(google_places)-added_fsq} +FSQ:{added_fsq} toplam:{len(google_places)}")
    return google_places, google_error


@bp.route("/room/<code>/search", methods=["POST"])
def search_places(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404

    participant = _current_participant(room["id"])
    if not participant:
        return jsonify({"error": "Yetkisiz"}), 403

    data = request.get_json(force=True)
    lat  = data.get("lat")
    lng  = data.get("lng")
    if lat is None or lng is None:
        return jsonify({"error": "Konum gerekli"}), 400

    radius = int(data.get("radius", Config.SEARCH_RADIUS_METERS))
    if radius not in Config.ALLOWED_RADII:
        radius = Config.SEARCH_RADIUS_METERS

    models.update_room_location(code, lat, lng)

    places, overpass_error = _combined_search(lat, lng, radius, room["category"])
    actual_radius = radius

    # Yeterli mekan yoksa radius'u otomatik genişlet — yeni sonuçları eskiye ekle
    existing_ids = {p["osm_id"] for p in places}
    for next_r in _EXPAND_RADII:
        if len(places) >= _MIN_PLACES or next_r <= radius:
            continue
        print(f"[Search] {len(places)} mekan < {_MIN_PLACES}, genisletiliyor: {actual_radius}m -> {next_r}m")
        expanded, err = _combined_search(lat, lng, next_r, room["category"])
        actual_radius = next_r
        for p in expanded:
            if p["osm_id"] not in existing_ids:
                places.append(p)
                existing_ids.add(p["osm_id"])
        if err and not overpass_error:
            overpass_error = err
        if len(places) >= _MIN_PLACES:
            break

    if overpass_error and not places:
        return jsonify({"error": overpass_error}), 502

    models.save_places(room["id"], places)
    models.update_room_status(code, "voting")

    saved = [dict(p) for p in models.get_room_places(room["id"])]
    socketio.emit("places_loaded", {
        "places": saved, "lat": lat, "lng": lng, "actual_radius": actual_radius,
    }, to=code)

    return jsonify({"places": saved, "actual_radius": actual_radius})


# ── Oylama ──────────────────────────────────────────────────────────────────

@bp.route("/room/<code>/vote", methods=["POST"])
def vote(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404
    if room["status"] not in ("voting", "waiting"):
        return jsonify({"error": "Oylama kapalı"}), 400

    participant = _current_participant(room["id"])
    if not participant:
        return jsonify({"error": "Yetkisiz"}), 403

    data     = request.get_json(force=True)
    place_id = data.get("place_id")
    value    = data.get("value")

    if value not in (1, -1):
        return jsonify({"error": "Geçersiz oy değeri"}), 400

    used = models.get_participant_vote_count(participant["id"], room["id"])
    if used >= Config.MAX_VOTES_PER_USER:
        return jsonify({"error": f"Maksimum {Config.MAX_VOTES_PER_USER} oy hakkını kullandınız"}), 400

    if not models.cast_vote(room["id"], participant["id"], place_id, value):
        return jsonify({"error": "Zaten oy kullandınız"}), 409

    models.update_score(participant["id"], Config.VOTE_ENGAGEMENT_BONUS)

    summary      = [dict(r) for r in models.get_vote_summary(room["id"])]
    participants = [dict(p) for p in models.get_room_participants(room["id"])]
    socketio.emit("vote_update", {"summary": summary, "participants": participants}, to=code)

    return jsonify({"ok": True, "summary": summary})


# ── Durum & Özet ────────────────────────────────────────────────────────────

@bp.route("/room/<code>/status")
def room_status(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404
    return jsonify({
        "status":       room["status"],
        "participants": [dict(p) for p in models.get_room_participants(room["id"])],
        "summary":      [dict(r) for r in models.get_vote_summary(room["id"])],
    })


@bp.route("/room/<code>/finish", methods=["POST"])
def finish(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404

    participant = _current_participant(room["id"])
    if not participant:
        return jsonify({"error": "Yetkisiz"}), 403
    if not participant["is_owner"]:
        return jsonify({"error": "Sadece oda sahibi oylamayı bitirebilir"}), 403

    models.update_room_status(code, "completed")
    score_log    = models.award_result_scores(room["id"])
    summary      = [dict(r) for r in models.get_vote_summary(room["id"])]
    participants = [dict(p) for p in models.get_room_participants(room["id"])]
    winner       = summary[0] if summary else None

    socketio.emit("show_results", {
        "winner": winner, "summary": summary,
        "participants": participants, "score_log": score_log,
    }, to=code)

    return jsonify({
        "ok": True, "winner": winner, "summary": summary,
        "participants": participants, "score_log": score_log,
    })


@bp.route("/stats")
def stats():
    return jsonify(models.get_stats())


# ── Flutter endpoint'leri (auth gerektirmez, oda kodu yeterli) ───────────────

def _flutter_body():
    """text/plain body'yi güvenilir şekilde parse et."""
    import json as _json
    return _json.loads(request.get_data(as_text=True) or "{}")


def _flutter_participant(data, room_id):
    """Body'deki token ile katılımcıyı bul; token yoksa None döner."""
    token = data.get("token")
    if not token:
        return None
    p = models.get_participant_by_token(token)
    if p and p["room_id"] == room_id:
        return p
    return None


@bp.route("/flutter/room/<code>/search", methods=["POST"])
def flutter_search(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404

    data   = _flutter_body()
    lat    = data.get("lat")
    lng    = data.get("lng")
    if lat is None or lng is None:
        return jsonify({"error": "Konum gerekli"}), 400

    radius = int(data.get("radius", Config.SEARCH_RADIUS_METERS))
    if radius not in Config.ALLOWED_RADII:
        radius = Config.SEARCH_RADIUS_METERS

    models.update_room_location(code, lat, lng)

    places, overpass_error = _combined_search(lat, lng, radius, room["category"])
    actual_radius = radius

    existing_ids = {p["osm_id"] for p in places}
    for next_r in _EXPAND_RADII:
        if len(places) >= _MIN_PLACES or next_r <= radius:
            continue
        print(f"[Flutter/Search] {len(places)} < {_MIN_PLACES}, genisletiliyor -> {next_r}m")
        expanded, err = _combined_search(lat, lng, next_r, room["category"])
        actual_radius = next_r
        for p in expanded:
            if p["osm_id"] not in existing_ids:
                places.append(p)
                existing_ids.add(p["osm_id"])
        if err and not overpass_error:
            overpass_error = err
        if len(places) >= _MIN_PLACES:
            break

    if overpass_error and not places:
        return jsonify({"error": overpass_error}), 502

    models.save_places(room["id"], places)
    models.update_room_status(code, "voting")

    saved = [dict(p) for p in models.get_room_places(room["id"])]
    socketio.emit("places_loaded", {
        "places": saved, "lat": lat, "lng": lng, "actual_radius": actual_radius,
    }, to=code)

    return jsonify({"places": saved, "actual_radius": actual_radius})


@bp.route("/flutter/room/<code>/vote", methods=["POST"])
def flutter_vote(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404
    if room["status"] not in ("voting", "waiting"):
        return jsonify({"error": "Oylama kapalı"}), 400

    data      = _flutter_body()
    place_id  = data.get("place_id")
    value     = data.get("value")

    if value not in (1, -1):
        return jsonify({"error": "Geçersiz oy değeri"}), 400

    participant = _flutter_participant(data, room["id"])
    if not participant:
        return jsonify({"error": "Geçersiz token"}), 403

    used = models.get_participant_vote_count(participant["id"], room["id"])
    if used >= Config.MAX_VOTES_PER_USER:
        return jsonify({"error": f"Maksimum {Config.MAX_VOTES_PER_USER} oy kullandınız"}), 400

    if not models.cast_vote(room["id"], participant["id"], place_id, value):
        return jsonify({"error": "Zaten oy kullandınız"}), 409

    models.update_score(participant["id"], Config.VOTE_ENGAGEMENT_BONUS)

    summary      = [dict(r) for r in models.get_vote_summary(room["id"])]
    participants = [dict(p) for p in models.get_room_participants(room["id"])]
    socketio.emit("vote_update", {"summary": summary, "participants": participants}, to=code)

    return jsonify({"ok": True, "summary": summary})


@bp.route("/flutter/room/<code>/finish", methods=["POST"])
def flutter_finish(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404

    data        = _flutter_body()
    participant = _flutter_participant(data, room["id"])
    if not participant:
        return jsonify({"error": "Geçersiz token"}), 403
    if not participant["is_owner"]:
        return jsonify({"error": "Sadece oda sahibi oylamayı bitirebilir"}), 403

    models.update_room_status(code, "completed")
    score_log    = models.award_result_scores(room["id"])
    summary      = [dict(r) for r in models.get_vote_summary(room["id"])]
    participants = [dict(p) for p in models.get_room_participants(room["id"])]
    winner       = summary[0] if summary else None

    socketio.emit("show_results", {
        "winner": winner, "summary": summary,
        "participants": participants, "score_log": score_log,
    }, to=code)

    return jsonify({
        "ok": True, "winner": winner, "summary": summary,
        "participants": participants, "score_log": score_log,
    })


@bp.route("/room/<code>/results")
def room_results(code):
    room = models.get_room(code)
    if not room:
        return jsonify({"error": "Oda bulunamadı"}), 404
    if room["status"] != "completed":
        return jsonify({"completed": False}), 200
    summary      = [dict(r) for r in models.get_vote_summary(room["id"])]
    participants = [dict(p) for p in models.get_room_participants(room["id"])]
    winner       = summary[0] if summary else None
    return jsonify({
        "completed":    True,
        "winner":       winner,
        "summary":      summary,
        "participants": participants,
    })
