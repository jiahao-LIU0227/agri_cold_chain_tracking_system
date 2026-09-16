"""高德 Web 服务 API 封装。

两个用途：
    1. 取规划路线的折线点（驾车路径规划）
    2. 搜周边 POI（加油站、服务区）

请求结果都会缓存到 data/cache/。原因有两个：
    - 高德个人 Key 有每日调用量限制，反复调会把额度耗光
    - 演示现场不一定有网，必须有本地兜底

取数据顺序：先查缓存 -> 没有才联网 -> 把结果写回缓存。
所以同一份数据只有第一次会真的发请求，之后都是读本地文件。

注意：这里用的是 AMAP_WEB_KEY，只在后端用。它不能返回给前端页面。
"""

import json
import os
import time

import requests

from app.models import Poi
from config import AMAP_WEB_KEY

AMAP_REST = "https://restapi.amap.com/v3"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")

# 高德个人 Key 有每秒请求数限制，连续调接口会报这些错误码。
# 碰到就等一会儿重试，别让一次超限把整批数据打断。
RETRY_INFCODES = {
    "10021",  # CUQPS_HAS_EXCEEDED_THE_LIMIT，每秒请求数超限
    "10019",  # CUQPS_HAS_EXCEEDED_THE_LIMIT（并发）
    "10020",  # CUQPS_HAS_EXCEEDED_THE_LIMIT（日调用量）
}
RETRY_TIMES = 4
RETRY_WAIT_S = 1.5


class AmapError(RuntimeError):
    """高德接口返回了错误，或者 Key 没配好。"""


# ============================================================
# 缓存
# ============================================================

def _cache_path(name):
    return os.path.join(CACHE_DIR, f"{name}.json")


def _read_cache(name):
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_cache(name, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clear_cache():
    """删掉全部缓存。想强制重新联网拉数据时用。"""
    if not os.path.isdir(CACHE_DIR):
        return 0
    n = 0
    for fname in os.listdir(CACHE_DIR):
        if fname.endswith(".json"):
            os.remove(os.path.join(CACHE_DIR, fname))
            n += 1
    return n


# ============================================================
# 底层调用
# ============================================================

def _call(path, params, cache_name=None):
    """调一次高德 REST 接口，带缓存和超限重试。

    requests 会用 UTF-8 编码参数，中文关键字（比如「加油站」）能正常传过去。
    用命令行 curl 测中文参数会因为控制台是 GBK 而失败，别拿那个结果下结论。
    """
    if not AMAP_WEB_KEY:
        raise AmapError("AMAP_WEB_KEY 没配置，去 .env 里填上。申请时服务平台要选「Web服务」。")

    if cache_name:
        cached = _read_cache(cache_name)
        if cached is not None:
            return cached

    data = None
    for attempt in range(RETRY_TIMES + 1):
        resp = requests.get(
            f"{AMAP_REST}/{path}",
            params={"key": AMAP_WEB_KEY, **params},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        # 高德用 status 字段表示业务成功与否，HTTP 200 不代表调用成功
        if data.get("status") == "1":
            break

        infocode = data.get("infocode")
        if infocode in RETRY_INFCODES and attempt < RETRY_TIMES:
            wait = RETRY_WAIT_S * (attempt + 1)
            print(f"  高德限流({infocode})，{wait:.1f} 秒后重试第 {attempt + 1} 次")
            time.sleep(wait)
            continue

        raise AmapError(f"高德接口报错: {data.get('info')} (infocode={infocode})")

    if cache_name:
        _write_cache(cache_name, data)
    return data


# ============================================================
# 路径规划
# ============================================================

def fetch_driving_route(origin, destination, cache_name=None):
    """取驾车规划路线。

    origin / destination 都是 "经度,纬度" 字符串。
    注意高德是**经度在前**，和平时说「纬度,经度」的习惯相反，传反了会跑到地球另一边去。

    返回 {"distance_m": 米, "duration_s": 秒, "points": [(lat, lng), ...]}
    返回的坐标已经是 GCJ-02，和高德地图、和本项目其他数据一致，不用转换。
    """
    data = _call(
        "direction/driving",
        {
            "origin": origin,
            "destination": destination,
            "extensions": "base",
            "strategy": 0,  # 速度最快
        },
        cache_name=cache_name,
    )

    path = data["route"]["paths"][0]

    points = []
    for step in path["steps"]:
        for pair in step["polyline"].split(";"):
            if not pair:
                continue
            lng, lat = pair.split(",")
            point = (float(lat), float(lng))
            # 相邻 step 会共用端点，去掉连续重复的点
            if not points or points[-1] != point:
                points.append(point)

    return {
        "distance_m": int(path["distance"]),
        "duration_s": int(path["duration"]),
        "points": points,
    }


# ============================================================
# 周边 POI 搜索
# ============================================================

def search_around_pois(location, poi_type, radius=5000, keywords=None,
                       types=None, limit=5, cache_name=None):
    """搜指定坐标周边的 POI。

    location 是 "经度,纬度"。
    keywords 和 types 二选一：
        keywords  中文关键字，比如「加油站」，好写但结果比较宽
        types     高德分类码，比如「010100」是加油站，更精确

    返回 Poi 对象列表。坐标已按 (lat, lng) 顺序放好。
    """
    if not keywords and not types:
        raise ValueError("keywords 和 types 至少要给一个")

    params = {"location": location, "radius": radius, "offset": limit, "page": 1}
    if keywords:
        params["keywords"] = keywords
    if types:
        params["types"] = types

    data = _call("place/around", params, cache_name=cache_name)

    pois = []
    for item in data.get("pois", [])[:limit]:
        if not item.get("location"):
            continue
        lng, lat = item["location"].split(",")
        pois.append(Poi(
            name=item["name"],
            poi_type=poi_type,
            latitude=float(lat),
            longitude=float(lng),
            radius_m=200,
            source="amap",
            amap_id=item.get("id") or None,
        ))
    return pois
