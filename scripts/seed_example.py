"""导入一条示例数据。

    python scripts/seed_example.py             # 优先用缓存，没有缓存才联网
    python scripts/seed_example.py --refresh   # 强制重新联网拉取

做三件事：

    1. 取规划路线和沿途 POI
    2. 把结果写进 examples/sample_route.json，作为断网时的兜底数据
    3. 往 MySQL 写一条运输任务、它的规划路线和 POI

重复运行是安全的：会先删掉同名任务再重新写入，结果一致。

断网时的行为：用例子里自带的 examples/sample_route.json，不会报错退出。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# 让脚本能 import 到项目根目录下的 config 和 app
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app import create_app, storage                                   # noqa: E402
from app.amap import AmapError, clear_cache, fetch_driving_route, search_around_pois  # noqa: E402
from app.geo import RoutePath, haversine                             # noqa: E402
from app.models import (CARGO_FROZEN, POI_GAS_STATION, POI_LOADING_DOCK,  # noqa: E402
                        POI_SERVICE_AREA, Poi, TransportTask)

CST = timezone(timedelta(hours=8))

TASK_NO = "T20260916001"
ORIGIN_NAME = "北京朝阳冷链中心"
DEST_NAME = "天津武清生鲜门店"
CARGO_TYPE = CARGO_FROZEN
TEMP_MIN = -25.0
TEMP_MAX = -18.0
PLANNED_START_HOUR = 8

# 起点和终点用 "经度,纬度"（高德接口的约定，经度在前）
ORIGIN_LNG_LAT = "116.443000,39.921800"
DEST_LNG_LAT = "117.056000,39.383000"

SAMPLE_ROUTE_PATH = os.path.join(BASE_DIR, "examples", "sample_route.json")

# 在路线全程的这几个位置附近搜 POI（0 是起点，1 是终点）
POI_SEARCH_AT = [0.2, 0.5, 0.8]

# 搜索半径。高德周边搜索是以一个点画圆，半径内可能有别的路上的 POI，
# 所以要再用 POI_MAX_OFFSET_M 过滤一次，只留下真正靠近路线的。
POI_SEARCH_RADIUS = 5000
POI_MAX_OFFSET_M = 1000

# 手写的 POI。高德分类里没有「中途装卸点」这种类别，
# 而停留分类算法需要它，所以手动补两个。
#
# 位置不写死经纬度，而是写成「沿路线走到百分之几、再往路边偏多少米」。
# 这样路线变了它们还是贴着路线。偏 120 米既像「仓库就在路边」，
# 又落在 200 米的围栏和偏航阈值里面，停车本身不会被判成偏航。
MANUAL_POIS = [
    {"name": "武清中转仓", "poi_type": POI_LOADING_DOCK, "at_ratio": 0.62, "offset_m": 120},
    {"name": "廊坊卸货点", "poi_type": POI_LOADING_DOCK, "at_ratio": 0.38, "offset_m": -120},
]


def build_manual_pois(path):
    """按「沿线比例 + 横向偏移」算出两个手写 POI 的经纬度。"""
    pois = []
    for m in MANUAL_POIS:
        at_m = path.total_length_m * m["at_ratio"]
        base_lat, base_lng, _ = path.position_at(at_m)
        lat, lng = path.offset_point(base_lat, base_lng, m["offset_m"],
                                     path.normal_angle_at(at_m))
        pois.append(Poi(name=m["name"], poi_type=m["poi_type"],
                        latitude=lat, longitude=lng,
                        radius_m=200, source="manual"))
    return pois


def pick_points_on_route(points, ratios):
    """从路线折线里按比例挑几个点，用来搜周边 POI。"""
    return [points[min(int(len(points) * r), len(points) - 1)] for r in ratios]


def distance_to_route_m(lat, lng, points):
    """一个点到规划路线的最短距离，米。

    折线点很密（这份数据 798 个点铺在 88 公里上，平均 110 米一个），
    所以直接取到最近折线点的距离就够了，不用算点到线段的垂距。
    """
    return min(haversine(lat, lng, p[0], p[1]) for p in points)


def build_sample(refresh=False):
    """取路线和 POI。优先联网（有缓存就读缓存），失败则用本地兜底文件。"""
    if refresh:
        n = clear_cache()
        print(f"已清空 {n} 个缓存文件")

    locked = None
    try:
        route = fetch_driving_route(ORIGIN_LNG_LAT, DEST_LNG_LAT, cache_name="route_bj_wq")
        print(f"路线来自高德: {route['distance_m'] / 1000:.1f} km, "
              f"{route['duration_s'] / 60:.0f} 分钟, {len(route['points'])} 个折线点")

        pois = []
        seen = set()
        dropped = 0
        for i, (lat, lng) in enumerate(pick_points_on_route(route["points"], POI_SEARCH_AT)):
            loc = f"{lng},{lat}"
            # cache_name 只用 ASCII，中文文件名换系统或打包时容易出问题
            for poi_type, kw, tp, tag in (
                (POI_GAS_STATION, None, "010100", "gas"),   # 010100 是加油站的分类码
                (POI_SERVICE_AREA, "服务区", None, "area"),
            ):
                try:
                    found = search_around_pois(loc, poi_type, radius=POI_SEARCH_RADIUS,
                                               keywords=kw, types=tp, limit=4,
                                               cache_name=f"poi_{tag}_{i}")
                except AmapError as exc:
                    print(f"  搜 {poi_type} 失败: {exc}")
                    continue
                for p in found:
                    key = p.amap_id or f"{p.name}|{p.latitude}"
                    if key in seen:
                        continue
                    seen.add(key)
                    # 只保留真正贴着路线的，避免地图上出现旁边其他路上的点
                    if distance_to_route_m(p.latitude, p.longitude, route["points"]) > POI_MAX_OFFSET_M:
                        dropped += 1
                        continue
                    pois.append(p)
        print(f"路线来自高德: 找到 {len(pois)} 个贴着路线的 POI"
              f"（另有 {dropped} 个离路线超过 {POI_MAX_OFFSET_M} 米，已丢弃）")

    except Exception as exc:
        print(f"联网取数据失败（{type(exc).__name__}: {exc}）")
        if not os.path.exists(SAMPLE_ROUTE_PATH):
            print(f"而且本地兜底文件 {SAMPLE_ROUTE_PATH} 也不存在，无法继续。")
            raise
        with open(SAMPLE_ROUTE_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        route = {"distance_m": saved["distance_m"],
                 "duration_s": saved["duration_s"],
                 "points": [tuple(p) for p in saved["points"]]}
        pois = [Poi(name=p["name"], poi_type=p["poi_type"],
                    latitude=p["latitude"], longitude=p["longitude"],
                    radius_m=p.get("radius_m", 200), source=p.get("source", "amap"),
                    amap_id=p.get("amap_id"))
                for p in saved["pois"]]
        locked = saved
        print(f"改用本地兜底文件: {len(route['points'])} 个折线点, {len(pois)} 个 POI")

    path = RoutePath(route["points"])
    pois += build_manual_pois(path)

    return route, pois, locked


def write_sample_file(route, pois, locked):
    """写 examples/sample_route.json，作为断网兜底。

    已经存在而且这次是读兜底文件来的，就不要覆盖了，免得把线上数据写坏。
    """
    if locked is not None:
        return

    data = {
        "name": f"{ORIGIN_NAME} -> {DEST_NAME}",
        "note": "示例路线，断网时兜底。坐标是 GCJ-02（高德坐标系）。",
        "coord_system": "gcj02",
        "origin": {"name": ORIGIN_NAME, **_split_lng_lat(ORIGIN_LNG_LAT)},
        "destination": {"name": DEST_NAME, **_split_lng_lat(DEST_LNG_LAT)},
        "distance_m": route["distance_m"],
        "duration_s": route["duration_s"],
        "points": [[lat, lng] for lat, lng in route["points"]],
        "pois": [
            {"name": p.name, "poi_type": p.poi_type,
             "latitude": p.latitude, "longitude": p.longitude,
             "radius_m": p.radius_m, "source": p.source, "amap_id": p.amap_id}
            for p in pois
        ],
    }
    os.makedirs(os.path.dirname(SAMPLE_ROUTE_PATH), exist_ok=True)
    with open(SAMPLE_ROUTE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"已写入 {SAMPLE_ROUTE_PATH}")


def _split_lng_lat(s):
    lng, lat = s.split(",")
    return {"latitude": float(lat), "longitude": float(lng)}


def seed_database(route, pois):
    """把任务、规划路线和 POI 写进 MySQL。

    先清掉旧数据再插入，保证重复运行结果一致：
        - 同名任务删掉，外键会级联删掉它的路线点
        - POI 是全局表，不挂在任务上，所以整表清空再写
    """
    app = create_app()
    with app.app_context():
        n = storage.delete_all_pois()
        print(f"已清空旧 POI {n} 条")

        old = [t for t in storage.list_tasks() if t.task_no == TASK_NO]
        for t in old:
            storage.delete_task(t.id)
            print(f"已删除旧的同名任务 id={t.id}")

        start = int(datetime.now(CST).replace(hour=PLANNED_START_HOUR, minute=0,
                                              second=0, microsecond=0).timestamp())
        # 计划结束时间 = 计划开始 + 纯行驶时间 + 预留 1 小时装卸和休息
        end = start + route["duration_s"] + 3600

        task = TransportTask(
            task_no=TASK_NO,
            origin_name=ORIGIN_NAME,
            dest_name=DEST_NAME,
            origin_lat=_split_lng_lat(ORIGIN_LNG_LAT)["latitude"],
            origin_lng=_split_lng_lat(ORIGIN_LNG_LAT)["longitude"],
            dest_lat=_split_lng_lat(DEST_LNG_LAT)["latitude"],
            dest_lng=_split_lng_lat(DEST_LNG_LAT)["longitude"],
            cargo_type=CARGO_TYPE,
            temp_min=TEMP_MIN,
            temp_max=TEMP_MAX,
            planned_start=start,
            planned_end=end,
        )
        task_id = storage.insert_task(task)
        print(f"已插入任务 id={task_id}  {TASK_NO}")

        n = storage.insert_route_points(task_id, route["points"])
        print(f"已插入规划路线 {n} 个点")

        storage.upsert_pois(pois)
        # upsert 的返回值受 ON DUPLICATE KEY UPDATE 影响会少算，直接报列表长度
        print(f"已写入 POI {len(pois)} 条（含手写 {len(MANUAL_POIS)} 条）")

        return task_id


def main():
    parser = argparse.ArgumentParser(description="导入示例任务数据")
    parser.add_argument("--refresh", action="store_true",
                        help="清空缓存，强制重新联网拉取路线和 POI")
    args = parser.parse_args()

    print("=" * 60)
    route, pois, locked = build_sample(refresh=args.refresh)
    write_sample_file(route, pois, locked)
    print("-" * 60)
    task_id = seed_database(route, pois)
    print("-" * 60)

    app = create_app()
    with app.app_context():
        print(f"任务数 {len(storage.list_tasks())}，"
              f"路线点 {len(storage.get_route_points(task_id))} 个，"
              f"POI {len(storage.list_pois())} 个")


if __name__ == "__main__":
    main()
