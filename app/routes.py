"""页面和 API 路由。

阶段一只实现 /api/health，用来确认配置和数据库是否正常。
后续阶段再补 simulate / tasks / track / analysis / report。
"""

from flask import Blueprint, jsonify

from app import db
from config import FLASK_PORT, missing_settings

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    """主页面。阶段四会换成真正的地图页面。"""
    return (
        "<h1>农产品冷链途迹监测系统</h1>"
        f"<p>服务已启动，端口 {FLASK_PORT}。</p>"
        "<p>检查配置和数据库：<a href='/api/health'>/api/health</a></p>"
    )


@bp.get("/api/health")
def health():
    """健康检查。出问题时第一个查它。

    会一次性报告：数据库连不连得上、哪些环境变量还没配。
    """
    ok, message = db.ping()
    missing = missing_settings()

    return jsonify({
        "status": "ok" if (ok and not missing) else "warning",
        "database": {"ok": ok, "message": message},
        "missing_settings": missing,
        "hint": "在 .env 里补齐上面列出的变量后重启服务" if missing else "",
    }), (200 if ok else 503)
