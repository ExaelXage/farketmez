from flask import Blueprint, request, jsonify, session
import requests
import models
from config import Config
from extensions import socketio
from concurrent.futures import ThreadPoolExecutor, as_completed

bp = Blueprint("api", __name__, url_prefix="/api")


def _current_participant(room_id=None):
    token = session.get("token")
    if not token:
        return None
    p = models.get_participant_by_token(token)
    if p and room_id and p["room_id"] != room_id:
        return None
    return p


# ── Overpass sorguları ───────────────────────────────────────────────────────

_OVERPASS_HEADERS = {
    "User-Agent": "Farketmez/1.0 (group decision app)",
    "Accept":     "application/json",
}

OVERPASS_QUERIES = {
    "food": """
[out:json][timeout:25];
(
  node["name"]["amenity"~"^(restaurant|cafe|fast_food|bar|pub|food_court|ice_cream|bakery|biergarten|juice_bar|canteen|diner|bbq|coffee_shop|lokanta|kebab_shop|nargile_cafe|hookah_lounge|patisserie|confectionery)$"](around:{radius},{lat},{lng});
  node["name"]["cuisine"](around:{radius},{lat},{lng});
  node["name"]["shop"~"^(bakery|pastry|deli|beverages|confectionery|food|butcher|cheese|chocolate|coffee|tea|wine|spices)$"](around:{radius},{lat},{lng});
  way["name"]["amenity"~"^(restaurant|cafe|fast_food|bar|pub|food_court|ice_cream|bakery|biergarten|canteen|lokanta|kebab_shop|nargile_cafe)$"](around:{radius},{lat},{lng});
  way["name"]["cuisine"](around:{radius},{lat},{lng});
  way["name"]["shop"~"^(bakery|pastry|food|confectionery)$"](around:{radius},{lat},{lng});
);
out body center {limit};
""",
    "activity": """
[out:json][timeout:25];
(
  node["name"]["leisure"~"^(bowling_alley|escape_game|park|fitness_centre|sports_centre|golf_course|miniature_golf|amusement_arcade|swimming_pool|water_park|ice_rink|playground|garden|beach_resort|sports_hall|dance)$"](around:{radius},{lat},{lng});
  node["name"]["amenity"~"^(cinema|theatre|nightclub|casino|arts_centre|community_centre|events_venue|conference_centre|planetarium)$"](around:{radius},{lat},{lng});
  node["name"]["tourism"~"^(attraction|theme_park|zoo|museum|gallery|aquarium)$"](around:{radius},{lat},{lng});
  way["name"]["leisure"~"^(park|fitness_centre|sports_centre|swimming_pool|stadium|water_park|garden|sports_hall|pitch|beach_resort)$"](around:{radius},{lat},{lng});
  way["name"]["amenity"~"^(cinema|theatre|nightclub|events_venue|arts_centre)$"](around:{radius},{lat},{lng});
  way["name"]["tourism"~"^(attraction|theme_park|zoo|museum|gallery|aquarium)$"](around:{radius},{lat},{lng});
);
out body center {limit};
""",
}


# ── Filtreler ────────────────────────────────────────────────────────────────

# Zincir market isimleri — başlangıç eşleşmesi (büyük/küçük harf yok sayılır)
_BLOCKED_CHAIN_NAMES = (
    "bim", "a101", "şok", "sok", "migros", "carrefoursa", "carrefour sa",
)

# Overpass shop/amenity tag değerleri
_BLOCKED_OSM_TAGS = frozenset({
    "supermarket", "convenience", "market",
})

# Foursquare category adında geçmesi yeterli anahtar kelimeler
_BLOCKED_FSQ_KEYWORDS = (
    "supermarket", "convenience store", "grocery store", "market",
)


def _is_blocked_name(name: str) -> bool:
    n = name.lower().strip()
    for chain in _BLOCKED_CHAIN_NAMES:
        if n == chain or n.startswith(chain + " "):
            return True
    return False


def _is_blocked_osm(tags: dict) -> bool:
    shop    = (tags.get("shop") or "").lower()
    amenity = (tags.get("amenity") or "").lower()
    return shop in _BLOCKED_OSM_TAGS or amenity in _BLOCKED_OSM_TAGS


def _is_blocked_fsq(cat_names: list) -> bool:
    for cat in cat_names:
        cat_lower = cat.lower()
        if any(kw in cat_lower for kw in _BLOCKED_FSQ_KEYWORDS):
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


# ── Konum & Mekan arama ─────────────────────────────────────────────────────

_MIN_PLACES    = 15
_EXPAND_RADII  = [5_000, 10_000, 20_000]  # otomatik genişleme adımları


def _combined_search(lat, lng, radius, category):
    """Overpass + Foursquare paralel çalıştır, sonuçları birleştir."""
    query = OVERPASS_QUERIES.get(category, OVERPASS_QUERIES["food"]).format(
        radius=radius, lat=lat, lng=lng,
        limit=Config.MAX_PLACES_PER_QUERY,
    )

    def _run_overpass():
        resp = requests.post(
            Config.OVERPASS_API_URL,
            data={"data": query},
            headers=_OVERPASS_HEADERS,
            timeout=28,
        )
        resp.raise_for_status()
        return resp.json().get("elements", [])

    overpass_elements, fsq_places = [], []
    overpass_error = None

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_overpass = pool.submit(_run_overpass)
        fut_fsq      = pool.submit(_fetch_foursquare, lat, lng, radius, category)

        try:
            overpass_elements = fut_overpass.result()
        except requests.exceptions.HTTPError as e:
            overpass_error = f"Overpass HTTP {e.response.status_code}"
        except Exception as e:
            overpass_error = f"Overpass API hatası: {e}"

        fsq_places = fut_fsq.result()

    print(f"[Foursquare] radius={radius}m -> {len(fsq_places)} mekan")

    seen, places = set(), []
    for el in overpass_elements:
        key = f"{el.get('type')}/{el.get('id')}"
        if key in seen:
            continue
        seen.add(key)

        tags = el.get("tags", {})
        name = tags.get("name", "").strip()
        if not name:
            continue

        if _is_blocked_name(name) or _is_blocked_osm(tags):
            continue

        if el["type"] == "way":
            c = el.get("center", {})
            elat, elng = c.get("lat"), c.get("lon")
        else:
            elat, elng = el.get("lat"), el.get("lon")

        addr_parts = [tags.get("addr:street",""), tags.get("addr:housenumber",""), tags.get("addr:city","")]
        address    = " ".join(p for p in addr_parts if p) or "—"

        places.append({
            "osm_id":   str(el.get("id", "")),
            "name":     name,
            "category": tags.get("amenity") or tags.get("leisure") or tags.get("tourism") or tags.get("shop") or category,
            "lat": elat, "lng": elng,
            "address": address,
        })

    existing_names = {p["name"].lower() for p in places}
    for fp in fsq_places:
        if fp["name"].lower() not in existing_names:
            places.append(fp)
            existing_names.add(fp["name"].lower())

    print(f"[Search] radius={radius}m -> Overpass:{len(overpass_elements)} FSQ:{len(fsq_places)} toplam:{len(places)}")
    return places, overpass_error


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
