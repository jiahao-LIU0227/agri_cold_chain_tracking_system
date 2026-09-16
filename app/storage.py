"""数据库读写。

建表语句在 sql/schema.sql，这里只负责增删查改。
所有函数都必须在一个 Flask 应用上下文里调用，例如：

    from app import create_app, storage
    app = create_app()
    with app.app_context():
        storage.list_tasks()
"""

from app import db
from app.models import Event, Poi, RoutePoint, Segment, TrackPoint, TransportTask


# ============================================================
# 运输任务
# ============================================================

def insert_task(task: TransportTask):
    """插入一个任务，返回新任务的 id。"""
    return db.insert(
        """
        INSERT INTO task (task_no, origin_name, dest_name,
                          origin_lat, origin_lng, dest_lat, dest_lng,
                          cargo_type, temp_min, temp_max,
                          planned_start, planned_end, coord_system, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (task.task_no, task.origin_name, task.dest_name,
         task.origin_lat, task.origin_lng, task.dest_lat, task.dest_lng,
         task.cargo_type, task.temp_min, task.temp_max,
         task.planned_start, task.planned_end, task.coord_system, task.created_at),
    )


def get_task(task_id: int):
    """按 id 取任务，取不到返回 None。"""
    row = db.query_one("SELECT * FROM task WHERE id = %s", (task_id,))
    return TransportTask.from_row(row) if row else None


def list_tasks():
    """列出全部任务，新的在前。"""
    rows = db.query_all("SELECT * FROM task ORDER BY id DESC")
    return [TransportTask.from_row(r) for r in rows]


def delete_task(task_id: int):
    """删除任务。轨迹点、路线点、分段、事件都会被外键级联删掉。"""
    return db.execute("DELETE FROM task WHERE id = %s", (task_id,))


# ============================================================
# 规划路线
# ============================================================

def insert_route_points(task_id: int, points):
    """批量写入规划路线点。

    points 是 (latitude, longitude) 的可迭代对象，seq 按顺序自动编号。
    """
    params = [
        (task_id, seq, lat, lng)
        for seq, (lat, lng) in enumerate(points)
    ]
    return db.executemany(
        "INSERT INTO route_point (task_id, seq, latitude, longitude) VALUES (%s, %s, %s, %s)",
        params,
    )


def get_route_points(task_id: int):
    """取某个任务的规划路线，按 seq 升序。"""
    rows = db.query_all(
        "SELECT * FROM route_point WHERE task_id = %s ORDER BY seq", (task_id,)
    )
    return [RoutePoint.from_row(r) for r in rows]


# ============================================================
# 轨迹点（数据量最大的表）
# ============================================================

def insert_track_points(task_id: int, points):
    """批量写入轨迹点。

    points 是 TrackPoint 列表。一次任务几千个点，一定要走批量插入。
    """
    params = [
        (task_id, p.ts, p.latitude, p.longitude, p.speed, p.heading,
         p.temperature, int(p.refrigerator_on), int(p.door_open), p.position_quality)
        for p in points
    ]
    return db.executemany(
        """
        INSERT INTO track_point (task_id, ts, latitude, longitude, speed, heading,
                                 temperature, refrigerator_on, door_open, position_quality)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        params,
    )


def get_track_points(task_id: int):
    """取某个任务的全部轨迹点，按时间升序。"""
    rows = db.query_all(
        "SELECT * FROM track_point WHERE task_id = %s ORDER BY ts, id", (task_id,)
    )
    return [TrackPoint.from_row(r) for r in rows]


def count_track_points(task_id: int):
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM track_point WHERE task_id = %s", (task_id,)
    )
    return row["n"]


def delete_track_points(task_id: int):
    """删掉某个任务的轨迹点。重新模拟之前要先清掉上一次的。"""
    return db.execute("DELETE FROM track_point WHERE task_id = %s", (task_id,))


# ============================================================
# 分段
# ============================================================

def insert_segment(task_id: int, segment: Segment):
    """写入一个分段，返回它的 id。

    分段一次只有十几条，所以一条一条插。这样能拿到自增 id，
    后面事件表要用 segment_id 指回分段，才能从事件反查「当时停在干什么」。
    """
    return db.insert(
        """
        INSERT INTO segment (task_id, segment_type, stop_type, start_ts, end_ts, duration_s,
                             start_lat, start_lng, end_lat, end_lng, distance_m, avg_speed)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (task_id, segment.segment_type, segment.stop_type,
         segment.start_ts, segment.end_ts, segment.duration_s,
         segment.start_lat, segment.start_lng, segment.end_lat, segment.end_lng,
         segment.distance_m, segment.avg_speed),
    )


def get_segments(task_id: int):
    """取某个任务的分段，按开始时间升序。"""
    rows = db.query_all(
        "SELECT * FROM segment WHERE task_id = %s ORDER BY start_ts", (task_id,)
    )
    return [Segment.from_row(r) for r in rows]


def delete_segments(task_id: int):
    """删掉某个任务的分段。重新分析之前要先清掉上次的结果。"""
    return db.execute("DELETE FROM segment WHERE task_id = %s", (task_id,))


# ============================================================
# 异常事件
# ============================================================

def insert_events(task_id: int, events):
    """批量写入异常事件。偏航和温度共用这张表。"""
    params = [
        (task_id, e.event_type, e.start_ts, e.end_ts, e.duration_s,
         e.max_distance_m, e.temp_min, e.temp_max,
         e.latitude, e.longitude, int(e.during_stop), e.segment_id)
        for e in events
    ]
    return db.executemany(
        """
        INSERT INTO event (task_id, event_type, start_ts, end_ts, duration_s,
                           max_distance_m, temp_min, temp_max,
                           latitude, longitude, during_stop, segment_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        params,
    )


def get_events(task_id: int, event_type=None):
    """取某个任务的事件，可以按类型过滤。"""
    sql = "SELECT * FROM event WHERE task_id = %s"
    params = [task_id]
    if event_type:
        sql += " AND event_type = %s"
        params.append(event_type)
    sql += " ORDER BY start_ts"
    return [Event.from_row(r) for r in db.query_all(sql, tuple(params))]


def delete_events(task_id: int):
    """删掉某个任务的事件。事件是每次分析算出来的，重算前先清空。"""
    return db.execute("DELETE FROM event WHERE task_id = %s", (task_id,))


# ============================================================
# POI
# ============================================================

def upsert_pois(pois):
    """写入 POI。已经有同 amap_id 的就更新，避免重复插入。

    amap_id 为 NULL 的手写 POI 不受唯一约束影响，可以有多条。
    """
    sql = """
        INSERT INTO poi (name, poi_type, latitude, longitude, radius_m, source, amap_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            poi_type = VALUES(poi_type),
            latitude = VALUES(latitude),
            longitude = VALUES(longitude),
            radius_m = VALUES(radius_m)
    """
    params = [
        (p.name, p.poi_type, p.latitude, p.longitude, p.radius_m, p.source, p.amap_id)
        for p in pois
    ]
    return db.executemany(sql, params)


def list_pois(poi_type=None):
    """列出 POI，可以按类型过滤。"""
    if poi_type:
        rows = db.query_all(
            "SELECT * FROM poi WHERE poi_type = %s ORDER BY id", (poi_type,)
        )
    else:
        rows = db.query_all("SELECT * FROM poi ORDER BY id")
    return [Poi.from_row(r) for r in rows]


def delete_all_pois():
    return db.execute("DELETE FROM poi")
