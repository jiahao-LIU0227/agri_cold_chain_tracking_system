"""测试用的公共小工具。

四个算法模块（segmentation / classification / deviation / temperature）
都是纯函数：给一堆轨迹点，返回分段或事件，不碰数据库也不调高德接口。
所以这里的测试不需要 MySQL、不需要联网，pytest 直接就能跑。

所有测试数据都按固定基准时刻构造，不使用当前时间，保证每次结果一致。
"""

import math
import os
import sys

import pymysql
import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import config                                                     # noqa: E402
from app.geo import (EARTH_RADIUS_M, from_local_meters,           # noqa: E402
                     to_local_meters)
from app.models import (CARGO_FROZEN, POI_GAS_STATION, SEGMENT_STOP,  # noqa: E402
                        Event, Poi, RoutePoint, Segment, TrackPoint,
                        TransportTask)

SCHEMA_PATH = os.path.join(BASE_DIR, "sql", "schema.sql")
TEST_DB_NAME = "cold_chain_test"

# 2026-09-16 08:00:00 UTC+8，随便挑的一个基准时刻。
# 测试里不用 time.time()，否则结果会随运行时间变化。
T0 = 1789516800

LAT0 = 39.9
LNG0 = 116.4

# 合成任务的默认温度范围（冷冻货）
TEMP_MIN = -25.0
TEMP_MAX = -18.0


def at_meters(east_m=0.0, north_m=0.0):
    """从基准点向东/向北移动若干米后的经纬度，返回 (纬度, 经度)。

    直接复用 app/geo.py 的换算函数：测试数据和分析算法用同一套换算，
    免得测试里自己写一套不同的一直测不出问题。
    """
    x0, y0 = to_local_meters(LAT0, LNG0, LAT0)
    return from_local_meters(x0 + east_m, y0 + north_m, LAT0)


def north_of(latitude, longitude, distance_m):
    """正北方向 distance_m 米处的点。

    距离按大圆距离精确成立（纬度差 1 度就是 R 弧长），所以可以拿来验算。
    """
    return latitude + math.degrees(distance_m / EARTH_RADIUS_M), longitude


def east_of(latitude, longitude, distance_m):
    """正东方向 distance_m 米处的点。

    围栏判断用的是 haversine，所以「边界上的点」必须按大圆距离放。
    这里解的是 haversine 在等纬度圈上的精确反函数：

        两点在同一纬度 phi 上时  d = 2R·asin(cos(phi)·sin(dλ/2))
        反解出                    dλ = 2·asin( sin(d/2R) / cos(phi) )

    要是图省事用「1 度经度 = 111320·cos(phi) 米」的平面近似来放这个点，
    200 米的围栏边界会偏二十几厘米，测出来就不是边界了，而是边界内侧。
    """
    d_lambda = 2 * math.asin(math.sin(distance_m / (2 * EARTH_RADIUS_M))
                             / math.cos(math.radians(latitude)))
    return latitude, longitude + math.degrees(d_lambda)


def point(ts, speed=10.0, east_m=0.0, north_m=0.0, temperature=-20.0,
          door_open=False, refrigerator_on=True, position_quality=1, task_id=1):
    """造一个轨迹点。默认在允许温度范围内。"""
    lat, lng = at_meters(east_m, north_m)
    return TrackPoint(
        task_id=task_id, ts=ts, latitude=lat, longitude=lng,
        speed=speed, heading=90.0, temperature=temperature,
        refrigerator_on=refrigerator_on, door_open=door_open,
        position_quality=position_quality,
    )


def make_points_at(times, speeds, **kwargs):
    """按给定的时间戳序列和速度序列造点。

    位置按速度向东积分，所以相邻两点的距离就是「速度 × 时间差」。
    边界测试（比如恰好 180 秒）用这个最清楚，因为时间戳完全由测试指定。
    """
    assert len(times) == len(speeds), "时间戳和速度的个数要对上"

    points = []
    east = 0.0
    for i, (ts, speed) in enumerate(zip(times, speeds)):
        if i > 0:
            east += speed * (ts - times[i - 1])
        points.append(point(ts, speed=speed, east_m=east, **kwargs))
    return points


def make_track(plan, interval=10, t0=T0, **kwargs):
    """按「一段一段」的方式造轨迹。

    plan 是 [(持续秒数, 速度 m/s), ...]，例如：
        [(600, 15.0), (300, 0.0), (600, 15.0)]
    表示先以 15 m/s 开 10 分钟，停 5 分钟，再开 10 分钟。
    """
    times, speeds = [], []
    ts = t0
    for seconds, speed in plan:
        steps = max(int(round(seconds / interval)), 1)
        for _ in range(steps):
            times.append(ts)
            speeds.append(speed)
            ts += interval
    return make_points_at(times, speeds, **kwargs)


def make_route(coords):
    """把 [(纬度, 经度), ...] 变成 RoutePoint 列表。"""
    return [RoutePoint(seq=i, latitude=lat, longitude=lng)
            for i, (lat, lng) in enumerate(coords)]


def straight_route_coords(east_end_m=20000.0, step_m=200.0, north_m=0.0):
    """一条正东方向的直线路线，返回 [(纬度, 经度), ...]。

    几点说明：
        - 分析算法要的是「折线点」，所以细分得密一点，
          免得点到线段的垂距被折线拐角影响
        - 这里返回的是朴素元组，因为 RoutePath 收的就是元组
        - 要给偏航检测用的话走下面的 straight_route()，那个包成了 RoutePoint
    """
    coords = []
    east = 0.0
    while east <= east_end_m:
        coords.append(at_meters(east, north_m))
        east += step_m
    return coords


def straight_route(east_end_m=20000.0, step_m=200.0, north_m=0.0):
    """同一条直线路线，包成 RoutePoint 列表，给 detect_deviations 这类函数用。"""
    return make_route(straight_route_coords(east_end_m, step_m, north_m))


def make_task(task_id=1, **overrides):
    """一个合成任务，默认是冷冻货。"""
    lat, lng = at_meters(0, 0)
    dest_lat, dest_lng = at_meters(20000, 0)
    defaults = dict(
        task_no="T-TEST", origin_name="测试起点", dest_name="测试终点",
        origin_lat=lat, origin_lng=lng, dest_lat=dest_lat, dest_lng=dest_lng,
        cargo_type=CARGO_FROZEN, temp_min=TEMP_MIN, temp_max=TEMP_MAX,
        planned_start=T0, planned_end=T0 + 14400,
    )
    defaults.update(overrides)
    task = TransportTask(**defaults)
    task.id = task_id
    return task


def make_poi(name="测试加油站", poi_type=POI_GAS_STATION, east_m=5000.0,
             north_m=0.0, radius_m=200, source="manual"):
    """一个 POI。默认放在路线正东方 5 公里处的路线上。"""
    lat, lng = at_meters(east_m, north_m)
    return Poi(name=name, poi_type=poi_type, latitude=lat, longitude=lng,
               radius_m=radius_m, source=source)


def track_from_positions(positions, interval=60, speed=15.0, t0=T0, **kwargs):
    """按给定的 (东向米, 北向米) 序列造轨迹。

    测偏航要能自己控制车往哪儿走，用 make_points_at 只能沿着一个方向积分，
    所以这里直接指定每个点的平面坐标。
    """
    return [
        point(t0 + interval * i, speed=speed, east_m=east, north_m=north, **kwargs)
        for i, (east, north) in enumerate(positions)
    ]


def make_stop_segment(latitude, longitude, duration_s, task_id=1):
    """一个停留段，首尾位置相同（停留本来就没有位移）。"""
    return Segment(
        segment_type=SEGMENT_STOP,
        start_ts=T0, end_ts=T0 + duration_s, duration_s=duration_s,
        start_lat=latitude, start_lng=longitude,
        end_lat=latitude, end_lng=longitude,
        task_id=task_id,
    )


def make_event(start_ts=T0, end_ts=T0 + 600, task_id=1):
    """一个偏航事件，只有测数据库往返时才用得到。"""
    return Event(
        event_type="deviation", start_ts=start_ts, end_ts=end_ts,
        duration_s=end_ts - start_ts, max_distance_m=520.5,
        latitude=LAT0, longitude=LNG0, task_id=task_id,
    )


@pytest.fixture
def task():
    return make_task()


@pytest.fixture
def route():
    return straight_route()


# ============================================================
# 数据库测试用的库
#
# 用单独的 cold_chain_test 库，不碰开发库 cold_chain 里的数据。
# 建表语句直接从 sql/schema.sql 读，只把库名换掉——建表逻辑只有一处，
# 测试库和开发库的表结构不会各写一份然后慢慢跑偏。
# ============================================================

def _schema_statements(database):
    """把 sql/schema.sql 读成一条条可以单独执行的语句，库名换成 database。"""
    with open(SCHEMA_PATH, encoding="utf-8") as fp:
        text = fp.read()

    text = text.replace(TEST_DB_NAME, "cold_chain")   # 防止重复替换
    text = text.replace("cold_chain", database)

    # 按分号切开，再把整行的 -- 注释去掉：只剩注释的语句执行会报空查询
    statements = []
    for chunk in text.split(";"):
        lines = [ln for ln in chunk.splitlines() if not ln.strip().startswith("--")]
        statement = "\n".join(lines).strip()
        if statement:
            statements.append(statement)
    return statements


@pytest.fixture(scope="session")
def test_db():
    """准备测试库，整个测试跑完删掉。连不上 MySQL 就跳过，不拖累其他测试。"""
    try:
        conn = pymysql.connect(
            host=config.DB_CONFIG["host"], port=config.DB_CONFIG["port"],
            user=config.DB_CONFIG["user"], password=config.DB_CONFIG["password"],
            charset=config.DB_CONFIG["charset"], autocommit=True,
        )
    except Exception as exc:
        pytest.skip(f"连不上 MySQL，跳过数据库相关测试：{type(exc).__name__}: {exc}")

    with conn.cursor() as cur:
        for statement in _schema_statements(TEST_DB_NAME):
            cur.execute(statement)
    conn.close()

    # 就地改字典：app/db.py 里 ``from config import DB_CONFIG`` 拿到的就是
    # 这个对象本身，改它等于改全局配置，所有 storage 函数都会连到测试库
    original = config.DB_CONFIG["database"]
    config.DB_CONFIG["database"] = TEST_DB_NAME

    yield TEST_DB_NAME

    config.DB_CONFIG["database"] = original
    conn = pymysql.connect(
        host=config.DB_CONFIG["host"], port=config.DB_CONFIG["port"],
        user=config.DB_CONFIG["user"], password=config.DB_CONFIG["password"],
        charset=config.DB_CONFIG["charset"], autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    conn.close()
