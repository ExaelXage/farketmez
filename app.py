import os
from flask import Flask, request, make_response
from flask_socketio import join_room, emit
from flask_cors import CORS
from extensions import socketio
from config import Config
import models

app = Flask(__name__)
app.config.from_object(Config)

# Flask-CORS baseline (SocketIO ve web sayfaları için)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

_async_mode = os.getenv("SOCKETIO_ASYNC_MODE", "threading")
socketio.init_app(app, cors_allowed_origins="*", async_mode=_async_mode)

from routes.room import bp as room_bp
from routes.api import bp as api_bp

app.register_blueprint(room_bp)
app.register_blueprint(api_bp)

models.init_db()


# ── CORS: tüm OPTIONS preflight'larını anında yanıtla ───────────────────────
# Flask-CORS + Flask-SocketIO + gevent kombinasyonu OPTIONS'ı gecikmeli işliyor;
# before_request ile kısaltma yaparak browser'ın bloke etmesini önlüyoruz.

@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Participant-Token, Accept, Authorization, X-Requested-With"
        )
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp


@app.after_request
def add_cors_headers(response):
    """Her yanıta CORS başlıkları ekle (preflight dışı istekler için)."""
    if request.method != "OPTIONS":
        response.headers["Access-Control-Allow-Origin"]  = "*"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Participant-Token, Accept, Authorization, X-Requested-With"
        )
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response


# ── Socket.IO events ─────────────────────────────────────────────────────────

@socketio.on("join_room_ws")
def on_join(data):
    code = data.get("code")
    if not code:
        return
    join_room(code)

    room = models.get_room(code)
    if room:
        participants = [dict(p) for p in models.get_room_participants(room["id"])]
        emit("participants_update", {"participants": participants}, to=code)


if __name__ == "__main__":
    models.init_db()
    socketio.run(app, debug=True, host="0.0.0.0", port=5000,
                 allow_unsafe_werkzeug=True, use_reloader=False)
