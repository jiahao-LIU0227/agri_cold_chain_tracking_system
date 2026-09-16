"""Flask 应用工厂。"""

import os

from flask import Flask

from app import db

# 项目根目录。templates/ 和 static/ 在根目录下，不在 app/ 里面，
# 所以要显式告诉 Flask 去哪找。
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
    )

    db.init_app(app)

    from app import routes
    app.register_blueprint(routes.bp)

    return app
