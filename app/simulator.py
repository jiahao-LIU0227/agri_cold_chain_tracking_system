"""运输过程模拟。

沿着规划路线造一条轨迹，并注入几种固定的情况，交给后面的分析算法去识别。

为什么需要模拟器：这个项目拿不到真实的冷链 GPS 数据，验收和演示又不能只给
一个空页面。所以造一条「看起来像真的」轨迹，并且把要考核的情况都造进去：
正常行驶、几种停留、偏航、温度异常。

一条重要原则：模拟器知道标准答案（哪里是加油、哪里在偏航），但分析算法不许偷看。
所以下面的计划表里只写**物理事实**（停多少秒、车门开不开、停在哪种 POI 里），
不写「这是加油」这种结论。结论要由 classification.py 自己从轨迹里推出来。
"""

import math
import random

from app.geo import RoutePath, bearing
from app.models import POI_GAS_STATION, POI_LOADING_DOCK, TrackPoint
from config import RANDOM_SEED, SAMPLE_INTERVAL

# ============================================================
# 行驶
# ============================================================

# 拿不到高德给的耗时时，用这个平均车速兜底。16 m/s ≈ 58 km/h
DEFAULT_CRUISE_SPEED = 16.0
# 车速随机波动 ±18%，免得整条轨迹速度一模一样
SPEED_JITTER = 0.18
# 停车前后多少米开始减速/加速，看起来像真的在开车
RAMP_M = 500.0
# 循环次数上限，纯粹是防止写错时死循环
MAX_TICKS = 200000

# 定位误差，米。真实 GPS 不会每次都报同一个点
GPS_JITTER_M = 3.0

# 故意插几个「漂移点」：车在动，但报了 0 速度。
# README 6.1 要求分段算法能靠前后点的连续性把这种点滤掉，
# 那就得先造出来给它滤。（这里是沿线比例）
DRIFT_AT_RATIO = (0.30, 0.72)

# ============================================================
# 温度
# ============================================================

# 目标温度 = 允许上限 - 2℃。冷冻货 temp_max = -18℃，所以目标是 -20℃
SETPOINT_MARGIN = 2.0
# 出发时比目标再低一点，留点余量
INITIAL_TEMP_OFFSET = -0.5
# 温度传感器噪声，℃
SENSOR_NOISE = 0.05

# 升温/降温速度，单位 ℃/分钟。
#
# 说明（报告里要写）：真实冷机制冷时降温很快，而停机、门关着时保温层撑得住，
# 实际升温只有 3~6 ℃/小时。这里为了在 100 多分钟的演示行程里造出明显异常，
# 把 WARM_RATE 放大到了 15 ℃/小时。这是演示参数，不是实测值。
COOL_RATE_PER_MIN = 0.6
WARM_RATE_PER_MIN = 0.25
DOOR_WARM_RATE_PER_MIN = 0.35
LEAK_RATE_PER_MIN = 0.02

# 制冷机故障的时间窗，按「走了全程的百分之几」表示
FRIDGE_OFF_WINDOWS = [(0.20, 0.40)]

# ============================================================
# 停车计划
# ============================================================

# 只写物理事实：
#   at_ratio    停在全程第几成处
#   poi_type    停进哪种 POI 里（None 表示停在路边）
#   duration_s  停多少秒
#   door_open   停车期间车门开不开
#
# 这几个停留是特意挑的，刚好覆盖 README 6.2 的几种分类：
#   40 秒  -> 不够 T_MIN(180 秒)，应该被合并回行驶段，不生成停留
#   300 秒 -> 停在加油站 POI 里，应该判成「加油」
#   1500 秒 -> 停在装卸点 POI 里且开着门，应该判成「中途装卸」
#   2100 秒 -> 周围没有 POI，超过 T_ABNORMAL(1800 秒)，应该判成「异常停留」
STOP_PLAN = [
    {"at_ratio": 0.18, "poi_type": None,             "duration_s": 40,
     "door_open": False, "note": "红灯，不该生成停留"},
    {"at_ratio": 0.09, "poi_type": POI_GAS_STATION,  "duration_s": 300,
     "door_open": False, "note": "加油站，5 分钟"},
    {"at_ratio": 0.62, "poi_type": POI_LOADING_DOCK, "duration_s": 1500,
     "door_open": True,  "note": "中途装卸，25 分钟且开门"},
    {"at_ratio": 0.85, "poi_type": None,             "duration_s": 2100,
     "door_open": False, "note": "路边久停，附近没有 POI"},
]

# 找 POI 停车时允许的位置误差：POI 的沿线位置和计划位置差多少米以内就算它
POI_MATCH_ALONG_M = 1500.0
# POI 离路线多远之内才敢拿它当停车点。超过这个距离，车为了停车会跑出
# ROUTE_BUFFER(200 米)，反而多出一个假偏航，得不偿失
POI_STOP_MAX_OFFSET_M = 150.0

# ============================================================
# 偏航计划
# ============================================================

# (起点比例, 终点比例, 最大横向偏移米)
# 偏移 450 米 > ROUTE_BUFFER(200 米)，所以一定会被判成偏航。
# 这一段和前后两次停留都不重叠，免得事件纠缠在一起说不清。
DEVIATION_PLAN = [(0.50, 0.58, 450.0)]


# ============================================================
# 计划 -> 具体停车点
# ============================================================

def _find_poi(path, pois, poi_type, want_along_m, used_names):
    """找一个适合停车的 POI：类型对得上、沿线位置够近、离路线不太远。"""
    best = None
    best_gap = None
    for poi in pois:
        if poi.poi_type != poi_type or poi.name in used_names:
            continue
        offset = path.distance_to_route_m(poi.latitude, poi.longitude)
        if offset > POI_STOP_MAX_OFFSET_M:
            continue
        along = path.distance_along_m(poi.latitude, poi.longitude)
        gap = abs(along - want_along_m)
        if gap > POI_MATCH_ALONG_M:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = poi, gap
    return best


def build_stops(path, pois):
    """把停车计划落成具体的停车点：停在第几米、经纬度是多少。

    poi_type 不为空的，优先把车停进那个 POI 里（现实里就是车开进加油站）。
    找不到合适的 POI 就退回停在路边，分类时会被判成休息或异常，也说得通。
    """
    stops = []
    used_names = set()

    for item in STOP_PLAN:
        want_along_m = path.total_length_m * item["at_ratio"]
        poi = None
        if item["poi_type"]:
            poi = _find_poi(path, pois, item["poi_type"], want_along_m, used_names)

        if poi is not None:
            used_names.add(poi.name)
            at_m = path.distance_along_m(poi.latitude, poi.longitude)
            latitude, longitude = poi.latitude, poi.longitude
        else:
            at_m = want_along_m
            latitude, longitude, _ = path.position_at(at_m)

        stops.append({
            "at_m": at_m,
            "duration_s": item["duration_s"],
            "door_open": item["door_open"],
            "latitude": latitude,
            "longitude": longitude,
            "poi_name": poi.name if poi else "",
            "note": item["note"],
        })

    # 按沿线里程排序，主循环才能顺序消费
    stops.sort(key=lambda s: s["at_m"])
    return stops


# ============================================================
# 各个物理量随时间怎么变
# ============================================================

def _cruise_speed(total_length_m, duration_s):
    """平均车速，米/秒。

    高德给的耗时里已经包含红绿灯和拥堵，直接用它反推车速，
    模拟出来的行驶时间才和规划时间对得上。
    """
    if duration_s and duration_s > 0:
        speed = total_length_m / duration_s
        # 钳到一个合理区间，防止高德返回离谱的值
        return max(4.0, min(speed, 30.0))
    return DEFAULT_CRUISE_SPEED


def _deviation_offset_m(dist_m, total_m):
    """当前行驶到 dist_m 处，应该横向偏离路线多少米。"""
    for start_ratio, end_ratio, max_m in DEVIATION_PLAN:
        start, end = start_ratio * total_m, end_ratio * total_m
        if start <= dist_m <= end:
            # 正弦曲线：从路线上平滑地荡出去，再平滑地荡回来
            progress = (dist_m - start) / (end - start)
            return max_m * math.sin(math.pi * progress)
    return 0.0


def _speed_at(dist_m, total_m, cruise, stops, since_stop_m, rng):
    """当前这一 tick 的车速，米/秒。"""
    speed = cruise * (1 + rng.uniform(-SPEED_JITTER, SPEED_JITTER))

    # 快到停车点了就减速
    for stop in stops:
        ahead = stop["at_m"] - dist_m
        if 0 < ahead < RAMP_M:
            speed = min(speed, cruise * (ahead / RAMP_M))
            break

    # 快到终点也减速，看起来像在找地方停车
    remaining = total_m - dist_m
    if 0 < remaining < RAMP_M:
        speed = min(speed, cruise * (remaining / RAMP_M))

    # 刚起步时速度是慢慢加上来的，不是一瞬间就 60 迈
    if since_stop_m < RAMP_M:
        speed = min(speed, cruise * (since_stop_m / RAMP_M))

    # 别让它停下来——真正的停留是上面那个停车计划管的，
    # 这里再兜一个下限，免得减速逻辑把行驶段压成 0 速度
    return max(speed, cruise * 0.15)


def _fridge_on(ratio, door_open):
    """制冷机开不开。

    开着门的时候制冷机是停的（省油，而且开着门制冷也没意义）；
    其余时间看有没有落在故障时间窗里。
    """
    if door_open:
        return False
    for start, end in FRIDGE_OFF_WINDOWS:
        if start <= ratio <= end:
            return False
    return True


def _next_temperature(temp, dt, fridge_on, door_open, setpoint):
    """下一个采样时刻的车厢温度，℃。

    制冷机开着就朝目标温度降；关着就往上升。开着门升得最快。
    已经降到目标温度了就保持，只留一点点保温层的渗热。
    """
    if door_open:
        return temp + DOOR_WARM_RATE_PER_MIN / 60.0 * dt
    if not fridge_on:
        return temp + WARM_RATE_PER_MIN / 60.0 * dt
    if temp > setpoint:
        return max(setpoint, temp - COOL_RATE_PER_MIN / 60.0 * dt)
    return temp + LEAK_RATE_PER_MIN / 60.0 * dt


# ============================================================
# 主流程
# ============================================================

def simulate_task(task, route_points, pois=None, duration_s=None, seed=None):
    """生成一个任务的完整轨迹。

    task          TransportTask，用到 planned_start 和温度上下限
    route_points  RoutePoint 列表（按 seq 排好），模拟器沿它前进
    pois          Poi 列表，用来决定把车停进哪个加油站/装卸点
    duration_s    高德给的纯行驶耗时，用来反推平均车速
    seed          随机种子，同一个种子结果完全一样，方便测试和复现

    返回 TrackPoint 列表，按时间升序。这个函数不碰数据库，方便单独测试。
    """
    rng = random.Random(RANDOM_SEED if seed is None else seed)

    path = RoutePath([(p.latitude, p.longitude) for p in route_points])
    total = path.total_length_m
    cruise = _cruise_speed(total, duration_s)
    stops = build_stops(path, pois or [])

    setpoint = task.temp_max - SETPOINT_MARGIN
    temp = setpoint + INITIAL_TEMP_OFFSET

    dt = SAMPLE_INTERVAL
    ts = task.planned_start
    dist = 0.0

    points = []
    stop_index = 0
    current_stop = None
    stop_remaining = 0
    since_stop_m = RAMP_M      # 起步时不用加速段
    drift_done = [False] * len(DRIFT_AT_RATIO)

    # 上一个「真实位置」，用来算航向角。GPS 抖动用它算就会乱转，所以另存一份
    prev_lat, prev_lng, route_heading = path.position_at(0)
    heading = route_heading

    while len(points) < MAX_TICKS:
        # ---------- 1. 决定这一 tick 的位置和状态 ----------
        if current_stop is not None:
            # 停在原地不动
            stop_remaining -= dt
            true_lat = current_stop["latitude"]
            true_lng = current_stop["longitude"]
            speed = 0.0
            door_open = current_stop["door_open"]
            if stop_remaining <= 0:
                current_stop = None
                since_stop_m = 0.0

        else:
            if dist >= total:
                break

            # 到停车点了就切进停留状态，这一 tick 不出点
            if stop_index < len(stops) and dist >= stops[stop_index]["at_m"]:
                current_stop = stops[stop_index]
                stop_index += 1
                stop_remaining = current_stop["duration_s"]
                continue

            speed = _speed_at(dist, total, cruise, stops, since_stop_m, rng)
            dist = min(dist + speed * dt, total)
            since_stop_m += speed * dt

            base_lat, base_lng, route_heading = path.position_at(dist)
            offset = _deviation_offset_m(dist, total)
            if offset > 0:
                # 偏航：从路线上往车左侧平移出去
                true_lat, true_lng = path.offset_point(
                    base_lat, base_lng, offset, path.normal_angle_at(dist)
                )
            else:
                true_lat, true_lng = base_lat, base_lng
            door_open = False

        # ---------- 2. 温度 ----------
        ratio = dist / total
        fridge_on = _fridge_on(ratio, door_open)
        temp = _next_temperature(temp, dt, fridge_on, door_open, setpoint)

        # ---------- 3. 航向角：用真实位置的前后变化算，拐弯和偏航都跟得上 ----------
        if speed > 0 and (abs(true_lat - prev_lat) > 1e-7 or abs(true_lng - prev_lng) > 1e-7):
            heading = bearing(prev_lat, prev_lng, true_lat, true_lng)

        # ---------- 4. 造几个 GPS 漂移点 ----------
        reported_speed = speed
        quality = 1
        for i, drift_ratio in enumerate(DRIFT_AT_RATIO):
            if not drift_done[i] and ratio >= drift_ratio and current_stop is None:
                drift_done[i] = True
                reported_speed = 0.0
                quality = 0
                break

        # ---------- 5. 加定位误差，出点 ----------
        lat = true_lat + rng.uniform(-GPS_JITTER_M, GPS_JITTER_M) / 111320.0
        lng = true_lng + rng.uniform(-GPS_JITTER_M, GPS_JITTER_M) / (
            111320.0 * math.cos(math.radians(true_lat))
        )
        temp_reported = temp + rng.uniform(-SENSOR_NOISE, SENSOR_NOISE)

        points.append(TrackPoint(
            task_id=task.id,
            ts=ts,
            latitude=round(lat, 7),
            longitude=round(lng, 7),
            speed=round(reported_speed, 2),
            heading=round(heading, 2),
            temperature=round(temp_reported, 2),
            refrigerator_on=fridge_on,
            door_open=door_open,
            position_quality=quality,
        ))

        prev_lat, prev_lng = true_lat, true_lng
        ts += dt

    return points
