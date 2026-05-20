import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY            = os.getenv("SECRET_KEY", "farketmez-dev-secret-2024")
    DEBUG                 = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    DATABASE              = os.getenv("DATABASE_URL", os.path.join(BASE_DIR, "farketmez.db"))
    OVERPASS_API_URL      = "https://overpass-api.de/api/interpreter"
    FOURSQUARE_API_URL    = "https://places-api.foursquare.com/places/search"
    FOURSQUARE_API_KEY    = os.getenv("FOURSQUARE_API_KEY", "")
    SEARCH_RADIUS_METERS  = 2000
    MAX_PLACES_PER_QUERY  = 200
    ALLOWED_RADII         = {500, 1000, 2000, 5000}

    # Oylama
    VOTE_ENGAGEMENT_BONUS = 2
    MAX_VOTES_PER_USER    = 3

    # Oylama sonu — sonuç bazlı puanlar
    PARTICIPATION_BONUS   =  5
    NO_VOTE_PENALTY       = -5
    WINNER_PICK_REWARD    = 25
    WINNER_OPPOSE_PENALTY = -20
    LOSER_LIKE_PENALTY    = -5
