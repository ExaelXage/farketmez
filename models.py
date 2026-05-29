import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from config import Config


@contextmanager
def get_db():
    conn = sqlite3.connect(Config.DATABASE, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        # Mevcut DB için migration: is_owner kolonu yoksa ekle
        try:
            conn.execute("ALTER TABLE participants ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rooms (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                name        TEXT    NOT NULL,
                category    TEXT    NOT NULL DEFAULT 'food',
                status      TEXT    NOT NULL DEFAULT 'waiting',
                lat         REAL,
                lng         REAL,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS participants (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                nickname    TEXT    NOT NULL,
                token       TEXT    NOT NULL UNIQUE,
                score       INTEGER NOT NULL DEFAULT 0,
                is_owner    INTEGER NOT NULL DEFAULT 0,
                joined_at   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS places (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                osm_id      TEXT,
                name        TEXT    NOT NULL,
                category    TEXT,
                lat         REAL,
                lng         REAL,
                address     TEXT
            );

            CREATE TABLE IF NOT EXISTS votes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id         INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                participant_id  INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                place_id        INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
                value           INTEGER NOT NULL,
                created_at      TEXT    NOT NULL,
                UNIQUE(participant_id, place_id)
            );
        """)


# --- Room ---

def create_room(name, category):
    code = uuid.uuid4().hex[:8].upper()
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO rooms (code, name, category, created_at) VALUES (?, ?, ?, ?)",
            (code, name, category, now)
        )
    return code


def get_room(code):
    with get_db() as conn:
        return conn.execute("SELECT * FROM rooms WHERE code = ?", (code,)).fetchone()


def update_room_status(code, status):
    with get_db() as conn:
        conn.execute("UPDATE rooms SET status = ? WHERE code = ?", (status, code))


def update_room_location(code, lat, lng):
    with get_db() as conn:
        conn.execute("UPDATE rooms SET lat = ?, lng = ? WHERE code = ?", (lat, lng, code))


# --- Participants ---

def nickname_exists_in_room(room_id, nickname):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM participants WHERE room_id = ? AND nickname = ?",
            (room_id, nickname),
        ).fetchone()
    return row is not None


def join_room(room_id, nickname, is_owner=False):
    token = uuid.uuid4().hex
    now   = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO participants (room_id, nickname, token, is_owner, joined_at) VALUES (?,?,?,?,?)",
            (room_id, nickname, token, int(is_owner), now)
        )
    return token


def get_participant_by_token(token):
    with get_db() as conn:
        return conn.execute("SELECT * FROM participants WHERE token = ?", (token,)).fetchone()


def get_room_participants(room_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM participants WHERE room_id = ? ORDER BY score DESC",
            (room_id,)
        ).fetchall()


def update_score(participant_id, delta):
    with get_db() as conn:
        conn.execute(
            "UPDATE participants SET score = score + ? WHERE id = ?",
            (delta, participant_id)
        )


# --- Places ---

def save_places(room_id, places):
    with get_db() as conn:
        conn.execute("DELETE FROM places WHERE room_id = ?", (room_id,))
        conn.executemany(
            "INSERT INTO places (room_id, osm_id, name, category, lat, lng, address) VALUES (?,?,?,?,?,?,?)",
            [(room_id, p["osm_id"], p["name"], p["category"], p["lat"], p["lng"], p["address"]) for p in places]
        )


def get_room_places(room_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM places WHERE room_id = ?", (room_id,)).fetchall()


# --- Votes ---

def cast_vote(room_id, participant_id, place_id, value):
    """value: +1 (like) or -1 (dislike). Returns True if inserted, False if already voted."""
    now = datetime.utcnow().isoformat()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO votes (room_id, participant_id, place_id, value, created_at) VALUES (?,?,?,?,?)",
                (room_id, participant_id, place_id, value, now)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_vote_summary(room_id):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.id, p.name, p.address, p.lat, p.lng, p.category,
                   COALESCE(SUM(v.value), 0) AS total_score,
                   COUNT(CASE WHEN v.value = 1  THEN 1 END) AS likes,
                   COUNT(CASE WHEN v.value = -1 THEN 1 END) AS dislikes
            FROM places p
            LEFT JOIN votes v ON v.place_id = p.id
            WHERE p.room_id = ?
            GROUP BY p.id
            ORDER BY total_score DESC
        """, (room_id,)).fetchall()
    return rows


def get_participant_vote_count(participant_id, room_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM votes WHERE participant_id = ? AND room_id = ?",
            (participant_id, room_id),
        ).fetchone()
    return row["cnt"] if row else 0


def get_participant_votes(participant_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT place_id, value FROM votes WHERE participant_id = ?",
            (participant_id,)
        ).fetchall()


def get_stats():
    with get_db() as conn:
        return {
            "total_rooms":  conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
            "active_rooms": conn.execute("SELECT COUNT(*) FROM rooms WHERE status != 'completed'").fetchone()[0],
            "total_users":  conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0],
            "total_votes":  conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0],
        }


def award_result_scores(room_id):
    """
    Oylama bitince çağrılır. Kazanan/kaybeden bazlı puanları hesaplar,
    DB'ye uygular ve her katılımcının puan değişim kaydını döner.
    """
    from collections import defaultdict

    summary = get_vote_summary(room_id)
    if not summary:
        return []

    winner_id  = summary[0]["id"]
    losing_ids = {r["id"] for r in summary[1:]}  # every non-winner

    with get_db() as conn:
        votes_rows = conn.execute(
            "SELECT participant_id, place_id, value FROM votes WHERE room_id = ?",
            (room_id,),
        ).fetchall()

        participants = conn.execute(
            "SELECT id, nickname FROM participants WHERE room_id = ?",
            (room_id,),
        ).fetchall()

    p_votes = defaultdict(list)
    for v in votes_rows:
        p_votes[v["participant_id"]].append((v["place_id"], v["value"]))

    score_log = []

    with get_db() as conn:
        for p in participants:
            pid      = p["id"]
            my_votes = p_votes[pid]
            delta    = 0
            breakdown = []

            if my_votes:
                delta += Config.PARTICIPATION_BONUS
                breakdown.append(f"+{Config.PARTICIPATION_BONUS} katılım bonusu")
            else:
                delta += Config.NO_VOTE_PENALTY
                breakdown.append(f"{Config.NO_VOTE_PENALTY} oy kullanmadı")

            for place_id, value in my_votes:
                if place_id == winner_id:
                    if value == 1:
                        delta += Config.WINNER_PICK_REWARD
                        breakdown.append(f"+{Config.WINNER_PICK_REWARD} kazanana oy verdi")
                    else:
                        delta += Config.WINNER_OPPOSE_PENALTY
                        breakdown.append(f"{Config.WINNER_OPPOSE_PENALTY} kazanana karşı oy verdi")
                elif place_id in losing_ids and value == 1:
                    delta += Config.LOSER_LIKE_PENALTY
                    breakdown.append(f"{Config.LOSER_LIKE_PENALTY} kaybedene oy verdi")

            conn.execute(
                "UPDATE participants SET score = score + ? WHERE id = ?",
                (delta, pid),
            )
            score_log.append({
                "participant_id": pid,
                "nickname":       p["nickname"],
                "delta":          delta,
                "breakdown":      breakdown,
            })

    return score_log
