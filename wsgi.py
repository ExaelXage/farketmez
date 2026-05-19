from gevent import monkey
monkey.patch_all()

from app import app  # noqa: E402
import models

models.init_db()
