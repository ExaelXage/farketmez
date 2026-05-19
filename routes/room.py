from flask import Blueprint, render_template, redirect, url_for, request, session, abort
import models
from config import Config

bp = Blueprint("room", __name__)


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/create", methods=["POST"])
def create():
    nickname = request.form.get("nickname", "").strip()
    category = request.form.get("category", "food")
    if not nickname:
        return redirect(url_for("room.index"))

    code = models.create_room(nickname, category)
    room = models.get_room(code)
    token = models.join_room(room["id"], nickname, is_owner=True)

    session["token"] = token
    return redirect(url_for("room.room_view", code=code))


@bp.route("/room/<code>")
def room_view(code):
    room = models.get_room(code)
    if not room:
        abort(404)

    token = session.get("token")
    participant = models.get_participant_by_token(token) if token else None

    # Visitor not yet joined
    if not participant or participant["room_id"] != room["id"]:
        return render_template("join.html", room=room, code=code)

    participants = [dict(p) for p in models.get_room_participants(room["id"])]
    my_votes = {v["place_id"]: v["value"] for v in models.get_participant_votes(participant["id"])}
    vote_summary = [dict(r) for r in models.get_vote_summary(room["id"])]
    room = dict(room)

    return render_template(
        "room.html",
        room=room,
        participant=dict(participant),
        participants=participants,
        my_votes=my_votes,
        vote_summary=vote_summary,
        max_votes=Config.MAX_VOTES_PER_USER,
    )


@bp.route("/room/<code>/join", methods=["POST"])
def join(code):
    room = models.get_room(code)
    if not room:
        abort(404)
    if room["status"] == "completed":
        return redirect(url_for("room.room_view", code=code))

    nickname = request.form.get("nickname", "").strip()
    if not nickname:
        return redirect(url_for("room.room_view", code=code))

    token = models.join_room(room["id"], nickname)
    session["token"] = token
    return redirect(url_for("room.room_view", code=code))
