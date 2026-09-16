"""页面和 API 路由。

API 先做完，页面再对着 API 写，这样前端可以单独调、单独测。
接口清单见 README 7，另外加了一个 /route：地图要画规划路线，
它和轨迹点不是一回事，单独一个接口更清楚。

返回的 JSON 键名统一用英文，值尽量是原始数据类型，
前端拿到就能直接喂给高德地图，不用再做转换。
"""

from dataclasses import asdict

from flask import Blueprint, jsonify, render_template, request

from app import db, pipeline, storage
from app.report import build_report
from config import (AMAP_JS_KEY, AMAP_JS_SECURITY_CODE, FLASK_PORT,
                    missing_settings)

bp = Blueprint("main", __name__)


def _error(message, status=400):
    return jsonify({"error": message}), status


@bp.get("/")
def index():
    """主页面：地图、回放、统计和异常列表。"""
    return render_template("index.html")


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
        "port": FLASK_PORT,
    }), (200 if ok else 503)


@bp.get("/api/config")
def config():
    """返回前端要用的高德 JS Key。

    只能给 JS API 的 Key 和安全密钥，它们本来就会出现在网页源码里，藏不住，
    靠高德控制台的域名白名单保护。

    绝不能返回 AMAP_WEB_KEY：那个 Key 在后端调接口时用，带的是你的调用额度，
    发到前端等于把额度送给别人。
    """
    return jsonify({
        "amap_js_key": AMAP_JS_KEY,
        "amap_js_security_code": AMAP_JS_SECURITY_CODE,
    })


@bp.get("/api/tasks")
def list_tasks():
    """任务列表，新的在前。"""
    tasks = storage.list_tasks()
    return jsonify({
        "tasks": [
            {
                "id": t.id,
                "task_no": t.task_no,
                "origin_name": t.origin_name,
                "dest_name": t.dest_name,
                "cargo_type": t.cargo_type,
                "temp_min": t.temp_min,
                "temp_max": t.temp_max,
                "planned_start": t.planned_start,
                "planned_end": t.planned_end,
                "track_points": storage.count_track_points(t.id),
            }
            for t in tasks
        ]
    })


def _get_task_or_404(task_id):
    task = storage.get_task(task_id)
    if task is None:
        return None, _error(f"任务 {task_id} 不存在", 404)
    return task, None


@bp.get("/api/tasks/<int:task_id>/route")
def get_route(task_id):
    """规划路线的折线点，地图上那条蓝线。"""
    task, error = _get_task_or_404(task_id)
    if error:
        return error

    points = storage.get_route_points(task_id)
    return jsonify({
        "task_id": task_id,
        "coord_system": task.coord_system,
        # 高德地图要的是 [经度, 纬度]，这里直接按这个顺序给，前端不用再翻
        "points": [[p.longitude, p.latitude] for p in points],
    })


@bp.get("/api/tasks/<int:task_id>/track")
def get_track(task_id):
    """实际轨迹点。回放、温度曲线、速度都从这一份数据里出。"""
    task, error = _get_task_or_404(task_id)
    if error:
        return error

    points = storage.get_track_points(task_id)
    return jsonify({
        "task_id": task_id,
        "coord_system": task.coord_system,
        "count": len(points),
        "points": [asdict(p) for p in points],
    })


@bp.get("/api/tasks/<int:task_id>/analysis")
def get_analysis(task_id):
    """分段、异常事件和统计。

    页面上要画的东西基本都在这里：停留点、偏航段、温度异常点、统计卡片。
    """
    task, error = _get_task_or_404(task_id)
    if error:
        return error

    points = storage.get_track_points(task_id)
    segments = storage.get_segments(task_id)
    events = storage.get_events(task_id)
    report = build_report(task, points, segments, events)

    return jsonify({
        "task_id": task_id,
        "segments": [asdict(s) for s in segments],
        "events": [asdict(e) for e in events],
        "summary": report.get("summary", {}),
    })


@bp.get("/api/tasks/<int:task_id>/report")
def get_report(task_id):
    """报告数据：任务信息、统计、停留构成、结论建议。"""
    task, error = _get_task_or_404(task_id)
    if error:
        return error

    report = build_report(
        task,
        storage.get_track_points(task_id),
        storage.get_segments(task_id),
        storage.get_events(task_id),
    )
    return jsonify(report)


@bp.post("/api/simulate")
def simulate():
    """生成一次模拟任务，并顺带完成分析。

    请求体可以不传，默认给最新的那个任务重新生成一遍：
        {"task_id": 1, "seed": 42}
    """
    body = request.get_json(silent=True) or {}

    task_id = body.get("task_id")
    if task_id is None:
        tasks = storage.list_tasks()
        if not tasks:
            return _error("还没有任务，先跑一次 python scripts/seed_example.py", 404)
        task_id = tasks[0].id

    try:
        task, points, segments, events = pipeline.simulate_task_data(
            task_id,
            duration_s=body.get("duration_s"),
            seed=body.get("seed"),
        )
    except ValueError as exc:
        return _error(str(exc), 400)

    report = build_report(task, points, segments, events)
    return jsonify({
        "task_id": task_id,
        "task_no": task.task_no,
        "point_count": len(points),
        "segment_count": len(segments),
        "event_count": len(events),
        "summary": report.get("summary", {}),
    })
