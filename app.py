import os
from flask import Flask
from flask_socketio import join_room, emit
from flask_cors import CORS
from extensions import socketio
from config import Config
import models

app = Flask(__name__)
app.config.from_object(Config)

# CORS: JSON API endpoint'leri için tüm origin'lere izin ver
# X-Participant-Token custom header'ı da dahil edilmeli (Flutter web preflight için)
CORS(app, resources={r"/api/*": {
    "origins": "*",
    "allow_headers": [
        "Content-Type", "X-Participant-Token",
        "Accept", "Authorization", "X-Requested-With",
    ],
    "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
}}, supports_credentials=False)

_async_mode = os.getenv("SOCKETIO_ASYNC_MODE", "threading")
socketio.init_app(app, cors_allowed_origins="*", async_mode=_async_mode)

from routes.room import bp as room_bp
from routes.api import bp as api_bp

app.register_blueprint(room_bp)
app.register_blueprint(api_bp)

models.init_db()

# ── Socket.IO events ────────────────────────────────────────────────────────

@socketio.on("join_room_ws")
def on_join(data):
    code = data.get("code")
    if not code:
        return
    join_room(code)

    # Odadaki güncel katılımcı listesini tüm odaya gönder
    room = models.get_room(code)
    if room:
        participants = [dict(p) for p in models.get_room_participants(room["id"])]
        emit("participants_update", {"participants": participants}, to=code)


if __name__ == "__main__":
    models.init_db()
    socketio.run(app, debug=True, host="0.0.0.0", port=5000,
                 allow_unsafe_werkzeug=True, use_reloader=False)
